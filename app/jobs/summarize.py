"""
Summarize a patient visit into a clinical note with the specified schema.
"""

import json
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime
from textwrap import dedent
from typing import Any, cast

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.services.doctor_access import doctor_access_in_force
from app.services.external_webhook_inbox import provider_log_ref
from app.services.terminology import (
    DEFAULT_TERMS,
    Terms,
    terminology_terms,
    transcript_label_note,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.ai_visit_note import REGENERATED_NOTE_SUPERSESSION, AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import Intake
from app.models.note_type_customization import (
    NoteTypeCustomization,
    PlanOrdering,
    PronounStyle,
    SubjectiveSource,
)
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlalchemy import and_, or_, update
from sqlmodel import Session, select


def _capture_owns_appointment(session: Session, appointment_id: TypeIDType) -> bool:
    # A retained binding owns preparation even before transcript import and
    # after revocation. A background job has no current capture authorization.
    return (
        session.exec(
            select(cast(Any, ScribeCaptureBinding.id))
            .where(ScribeCaptureBinding.appointment_id == appointment_id)
            .limit(1)
        ).first()
        is not None
    )


def perform(
    transcript_id: str | TypeIDType,
    schema_id: str | TypeIDType,
    *,
    note_policy: str | None = None,
):
    log.info(
        "starting summarization",
        transcript_ref=provider_log_ref(str(transcript_id)),
        schema_ref=provider_log_ref(str(schema_id)),
    )

    trigger = AIVisitTranscript.get(id=transcript_id)
    assert trigger

    doctor = Doctor.get(id=trigger.doctor_id)
    if doctor is None or not doctor_access_in_force(doctor):
        log.info(
            "summarization skipped: access unavailable",
            event_code="summarization_access_not_in_force",
            transcript_ref=provider_log_ref(str(transcript_id)),
        )
        return

    with get_session() as session:
        capture_owned = _capture_owns_appointment(
            cast(Session, session), trigger.appointment_id
        )
    if capture_owned:
        # Captures use explicit versioned sources. This background path merges
        # independent recordings and carries no current capture authority;
        # the explicit capture-note receiver owns the appointment's notes.
        log.info(
            "captured working source requires explicit note preparation",
            event_code="capture_working_explicit_preparation_required",
            transcript_ref=provider_log_ref(str(trigger.id)),
        )
        return

    schema = DoctorNoteSchema.get(id=schema_id)
    assert schema

    # Load every transcript tied to this appointment so back-to-back
    # segments (Mode A) collapse into one note. Ordering by created_at
    # preserves the clinical timeline; segment markers tell the LLM
    # where one recording ended and the next began.
    all_transcripts = cast(
        list[AIVisitTranscript],
        list(
            AIVisitTranscript.where(
                AIVisitTranscript.appointment_id == trigger.appointment_id,
                cast(Any, AIVisitTranscript.deleted_at).is_(None),
            )
            .order_by(
                cast(Any, AIVisitTranscript.created_at).asc(),
                cast(Any, AIVisitTranscript.id).asc(),
            )
            .all()
        ),
    )
    latest = all_transcripts[-1]

    # Idempotency: when a note already exists and is linked to the
    # current latest transcript, the merged content hasn't changed, so
    # there's nothing to regenerate. Celery retries and duplicate
    # webhook deliveries hit this guard.
    existing_note = AIVisitNote.get(
        (AIVisitNote.appointment_id == trigger.appointment_id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
        & (cast(Any, AIVisitNote.superseded_at).is_(None))
    )
    if existing_note and existing_note.ai_transcript_id == latest.id:
        log.info(
            "note already reflects latest transcript, skipping summarization",
            appointment_ref=provider_log_ref(str(trigger.appointment_id)),
            schema_ref=provider_log_ref(str(schema_id)),
        )
        return

    # Get most recently updated example note for this schema (optional)
    example = cast(
        DoctorNoteExample | None,
        DoctorNoteExample.select()
        .where(DoctorNoteExample.doctor_note_schema_id == schema_id)
        .order_by(cast(Any, DoctorNoteExample.updated_at).desc())
        .limit(1)
        .first(),
    )

    # TODO Validate the example note matches the schema.

    merged = _merge_transcripts(all_transcripts)

    customization = NoteTypeCustomization.get(doctor_note_schema_id=schema.id)

    log.info(
        "generating clinical note",
        segment_count=len(all_transcripts),
        merged_duration=merged.duration,
    )
    policy_args = {}
    if note_policy is not None:
        from app.services import zoom_bot_note_policy
        from app.services.zoom_bot_note_grounding import segment_boundaries

        if note_policy == zoom_bot_note_policy.POLICY and zoom_bot_note_policy.eligible(
            trigger, doctor
        ):
            policy_args["bot_fidelity"] = True
            policy_args["bot_segment_boundaries"] = segment_boundaries(all_transcripts)
    note = generate_clinical_note(
        transcript=merged,
        example_note=example,
        note_schema=schema,
        customization=customization,  # None -> platform defaults
        **policy_args,
    )

    log.info("augmenting clinical note")
    note = augment_clinical_note(original=note, transcript=merged)

    # CC == cheif complaint.
    appointment_name = note.get("CC", "General Appointment")

    # TODO unclear if we definitely want the intake appointment name to always override the appointment_name above
    # intake will not always exist, but if it does, let's pull the appointment name
    intake = Intake.get(appointment_id=trigger.appointment_id)
    if intake and intake.cleaned_external_intake_data:
        data = intake.cleaned_external_intake_data
        appointment_name = f"{data.appointment_type} -- {data.reason_for_visit}"

    _finalize_generated_note(
        trigger=trigger,
        schema=schema,
        generated_through_transcript_id=latest.id,
        note=note,
        appointment_name=appointment_name,
    )


def _finalize_generated_note(
    *,
    trigger: AIVisitTranscript,
    schema: DoctorNoteSchema,
    generated_through_transcript_id: TypeIDType,
    note: dict[str, str],
    appointment_name: str,
    authorize_finalization: Callable[[Session], None] | None = None,
    replace_note_ref: TypeIDType | None = None,
    persist_provenance: Callable[[Session, AIVisitNote], None] | None = None,
) -> bool:
    """Commit a generated note only while its transcript snapshot is current.

    The appointment row is the shared serialization lock for transcript ingest
    and note finalization.  Recall transcript ingest takes the same lock before
    inserting a new segment, so a worker cannot validate an old snapshot while
    a newer segment is being committed. The database partial unique index on
    current ``(appointment_id, doctor_note_schema_id)`` rows is the final
    duplicate guard for broker redelivery and concurrent workers.

    Returns ``True`` when a new note was committed and ``False`` when another
    worker already committed this snapshot or this worker generated from a
    snapshot that is no longer current.
    """

    with get_session() as session:
        if authorize_finalization is not None:
            # Capture callers acquire their normal authority locks before the
            # shared appointment lock, and hold them through publication.
            authorize_finalization(cast(Session, session))
        appointment = session.exec(
            select(Appointment)
            .where(Appointment.id == trigger.appointment_id)
            .with_for_update()
        ).one()
        if authorize_finalization is None and _capture_owns_appointment(
            cast(Session, session), appointment.id
        ):
            # A capture may have been bound while this background model call
            # was in flight. Recheck under the publication lock before writing.
            return False
        source_query = select(AIVisitTranscript).where(
            AIVisitTranscript.appointment_id == trigger.appointment_id,
            cast(Any, AIVisitTranscript.deleted_at).is_(None),
        )
        if authorize_finalization is not None:
            # The capture callback checks the current Scribe source under its
            # authority locks. A separate Recall/upload row cannot replace or
            # permanently block that exact source's note publication.
            source_query = source_query.where(
                AIVisitTranscript.id == generated_through_transcript_id
            )
        else:
            source_query = source_query.order_by(
                cast(Any, AIVisitTranscript.created_at).desc(),
                cast(Any, AIVisitTranscript.id).desc(),
            ).limit(1)
        current_latest = session.exec(source_query).one_or_none()
        existing_note = session.exec(
            select(AIVisitNote).where(
                AIVisitNote.appointment_id == trigger.appointment_id,
                AIVisitNote.doctor_note_schema_id == schema.id,
                cast(Any, AIVisitNote.superseded_at).is_(None),
            )
        ).one_or_none()

        if (
            current_latest is None
            or current_latest.id != generated_through_transcript_id
        ):
            log.info(
                "stale visit note generation skipped",
                appointment_ref=provider_log_ref(str(trigger.appointment_id)),
                schema_ref=provider_log_ref(str(schema.id)),
            )
            return False

        if (
            existing_note is not None
            and existing_note.ai_transcript_id == generated_through_transcript_id
            and existing_note.id != replace_note_ref
        ):
            log.info(
                "visit note generation already committed",
                appointment_ref=provider_log_ref(str(trigger.appointment_id)),
                schema_ref=provider_log_ref(str(schema.id)),
            )
            return False

        if not _finalization_scope_is_consistent(
            appointment=appointment,
            trigger=trigger,
            schema=schema,
            latest=current_latest,
            existing_note=existing_note,
        ):
            log.info(
                "visit note finalization scope validation failed",
                event_code="visit_note_finalization_scope_invalid",
                appointment_ref=provider_log_ref(str(trigger.appointment_id)),
                schema_ref=provider_log_ref(str(schema.id)),
            )
            return False

        if existing_note is not None and _restore_frontier_blocks_regeneration(
            session=session,
            appointment=appointment,
            existing_note=existing_note,
        ):
            log.info(
                "administratively restored visit note retained",
                event_code="visit_note_restore_frontier_preserved",
                appointment_ref=provider_log_ref(str(trigger.appointment_id)),
                schema_ref=provider_log_ref(str(schema.id)),
            )
            return False

        # Preserve the prior generated output as retained history. Explicitly
        # carry its old updated_at value through the SQL update: the generic
        # timestamp mixin otherwise treats classification as a content edit.
        if existing_note is not None:
            session.execute(
                update(cast(Any, AIVisitNote))
                .where(cast(Any, AIVisitNote.id) == existing_note.id)
                .values(
                    superseded_at=datetime.now(UTC),
                    supersession_reason=REGENERATED_NOTE_SUPERSESSION,
                    updated_at=existing_note.updated_at,
                )
            )
            session.flush()

        log.info(
            "saving visit note",
            appointment_ref=provider_log_ref(str(trigger.appointment_id)),
            schema_ref=provider_log_ref(str(schema.id)),
        )
        saved_note = AIVisitNote(
            doctor_id=trigger.doctor_id,
            appointment_id=trigger.appointment_id,
            ai_transcript_id=generated_through_transcript_id,
            doctor_note_schema_id=schema.id,
            note=note,
        )
        session.add(saved_note)
        session.flush()
        if persist_provenance is not None:
            persist_provenance(cast(Session, session), saved_note)
        appointment.appointment_name = appointment_name
        session.add(appointment)
        session.commit()
        return True


def _finalization_scope_is_consistent(
    *,
    appointment: Appointment,
    trigger: AIVisitTranscript,
    schema: DoctorNoteSchema,
    latest: AIVisitTranscript,
    existing_note: AIVisitNote | None,
) -> bool:
    """Require every row used for finalization to share one clinical scope."""

    expected = (
        appointment.id,
        appointment.doctor_id,
        appointment.patient_id,
    )
    if (
        (trigger.appointment_id, trigger.doctor_id, trigger.patient_id) != expected
        or (latest.appointment_id, latest.doctor_id, latest.patient_id) != expected
        or schema.doctor_id != appointment.doctor_id
    ):
        return False
    return existing_note is None or (
        existing_note.appointment_id == appointment.id
        and existing_note.doctor_id == appointment.doctor_id
        and existing_note.doctor_note_schema_id == schema.id
    )


def _restore_frontier_blocks_regeneration(
    *,
    session: Session,
    appointment: Appointment,
    existing_note: AIVisitNote,
) -> bool:
    """Protect an administrative restore from delayed generation retries.

    A restore records the latest active transcript visible while holding the
    appointment lock. A worker generated from that transcript (or an older
    one) must not immediately overwrite the operator's choice. A genuinely
    later active transcript is allowed to produce a new retained version.
    """

    frontier_id = existing_note.restore_frontier_transcript_id
    if frontier_id is None:
        return False
    frontier = session.exec(
        select(AIVisitTranscript).where(AIVisitTranscript.id == frontier_id)
    ).one_or_none()
    if frontier is None or (
        frontier.appointment_id,
        frontier.doctor_id,
        frontier.patient_id,
    ) != (
        appointment.id,
        appointment.doctor_id,
        appointment.patient_id,
    ):
        return True

    newer_active_id = session.exec(
        select(cast(Any, AIVisitTranscript.id))
        .where(
            AIVisitTranscript.appointment_id == appointment.id,
            cast(Any, AIVisitTranscript.deleted_at).is_(None),
            or_(
                cast(Any, AIVisitTranscript.created_at) > frontier.created_at,
                and_(
                    cast(Any, AIVisitTranscript.created_at) == frontier.created_at,
                    cast(Any, AIVisitTranscript.id) > frontier.id,
                ),
            ),
        )
        .limit(1)
    ).first()
    return newer_active_id is None


def _merge_transcripts(
    transcripts: list[AIVisitTranscript],
) -> AIVisitTranscript:
    """Combine N transcript rows into a single in-memory AIVisitTranscript
    that the LLM helpers can consume unchanged. Segment markers tell the
    model where one recording ended and the next began so it can reason
    about gaps. Durations sum so the 'time spent in patient care' line
    that augment_clinical_note appends covers the whole visit."""
    first = transcripts[0]
    merged_entries: list[TranscriptEntry] = []
    total_duration = 0
    for i, t in enumerate(transcripts):
        if i > 0:
            merged_entries.append(
                TranscriptEntry(
                    speaker="system",
                    content=f"[Segment {i + 1} starts]",
                    startTime=0,
                    endTime=0,
                )
            )
        merged_entries.extend(t.transcript_data)
        total_duration += t.duration or 0

    # Construct without saving — this row exists only to feed the LLM
    # helpers; persisted state lives in the underlying transcripts.
    return AIVisitTranscript(
        doctor_id=first.doctor_id,
        appointment_id=first.appointment_id,
        patient_id=first.patient_id,
        duration=total_duration,
        transcript_data=merged_entries,
    )


perform_celery = celery_app.task(perform)
perform_bot_celery = celery_app.task(
    name="app.jobs.summarize.perform_bot",
    autoretry_for=(),
    max_retries=0,
)(perform)


def queue(*args, **kwargs):
    log.info("queuing summarization job")
    from app.services.zoom_bot_note_policy import POLICY

    if kwargs.get("note_policy") == POLICY:
        # The pilot has one generation attempt, including transport or commit
        # failures. A broker redelivery still uses the durable note replay guard.
        perform_bot_celery.delay(*args, **kwargs)
        return
    perform_celery.delay(*args, **kwargs)


def _pronoun_instruction(style: PronounStyle, t: Terms = DEFAULT_TERMS) -> str:
    if style == PronounStyle.infer_from_transcript:
        return f'Pronouns: When the transcript reveals the {t.patient}\'s pronouns -- via self-identification, the {t.provider}\'s use of "she/her" or "he/him" when referring to the {t.patient}, or other unambiguous context -- the note MUST use those pronouns when referring to the {t.patient}. Do not default to "the {t.patient}" everywhere when pronouns are clear from the transcript. Use "they/them" ONLY when no pronoun usage is evident anywhere in the transcript. This overrides any clinical convention of defaulting to "the {t.patient}" -- mix in the {t.patient}\'s actual pronouns when they are known, even if the example note\'s style differs on this point.'
    return (
        f'Pronouns: refer to the {t.patient} as "the {t.patient}"; do not infer pronouns from '
        "the transcript."
    )


def _subjective_instruction(source: SubjectiveSource, t: Terms = DEFAULT_TERMS) -> str:
    if source == SubjectiveSource.patient_only:
        return f'Subjective section: source ONLY from lines spoken by the {t.patient} (lines beginning with "Patient:" in the transcript above). The {t.provider}\'s interpretations, diagnostic impressions, follow-up questions, and clinical observations belong in the Assessment or Objective sections, never in Subjective. Phrasings like "the {t.patient} reports X" or "the {t.patient} denies Y" are acceptable as long as X and Y themselves came from a Patient: line.'
    return (
        f"Subjective section: capture the {t.patient}'s narrative. You may incorporate "
        f"history elicited by the {t.provider}'s questions, framed as the {t.patient}'s report "
        f'(e.g. "the {t.patient} reports X"). Keep diagnostic impressions and clinical '
        "observations in the Assessment or Objective sections."
    )


def _ordered_instructions(
    c: NoteTypeCustomization, t: Terms = DEFAULT_TERMS
) -> list[str]:
    out: list[str] = []
    out.append(
        f"Keep in mind your BIO and the kind of visit that the {t.patient} is coming in for. This will determine what information is most important to include in your note."
    )
    out.append(
        "Carefully review the transcript and extract all relevant medical information."
    )
    grouping_lines = [
        "Organize the information into the appropriate sections of the clinical note schema.",
        "  - Within each section, you should group information based on condition.",
        f"  - E.g. if the {t.patient} discusses diabetes and hypertension, and 3 steps in the plan relate to diabetes and 2 steps relate to hypertension, the plan should list the 3 diabetes steps, THEN the 2 hyptertension steps.",
        f"  - Sort based on importance of the condition. If hypertension is the {t.patient}'s main concern, it should be listed first.",
        "  - Be consistent with the order of conditions across fields. I.e. if anxiety is covered first in the plan, it should also be covered first in the assessment, subjective, and objective sections.",
    ]
    if c.plan_ordering == PlanOrdering.patient_then_doctor:
        grouping_lines.append(
            f"  - In the Plan section specifically, within each condition's bullets, list {t.patient}-actionable items (e.g. medications to take, lifestyle changes, symptoms to monitor, follow-ups the {t.patient} must initiate) before {t.provider}-actionable items (e.g. labs to order, referrals to send, follow-up calls the {t.provider} must place). This makes it easier for the {t.patient} to scan their own to-dos."
        )
    grouping_lines.append(
        '  - Do not create subsections within the fields for the conditions. Prefer "flat" lists of items unless absolutely necessary.'
    )
    out.append("\n".join(grouping_lines))
    out.append("Use clear, concise, and professional medical language.")
    out.append(
        "Avoid including any irrelevant personal details or small talk from the transcript."
    )
    out.append(
        f'If certain information is not provided in the transcript, leave that section blank or write "Not mentioned in transcript" as appropriate. E.g. if the {t.patient} does not mention respiratory problems, do NOT assume that they do not have them.'
    )
    out.append(
        "Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information."
    )
    out.append(_pronoun_instruction(c.pronoun_style, t))
    out.append(_subjective_instruction(c.subjective_source, t))
    if c.flag_new_medications:
        out.append(
            f'Medications section: when this section exists in the schema, list both medications the {t.patient} is already taking (from "Patient:" lines) AND medications the {t.provider} is prescribing or initiating during this visit. Tag newly-prescribed items inline with "(new this visit)" so they can be distinguished from the {t.patient}\'s existing medication list at a glance. Do not put newly-prescribed meds only in the Plan section -- the Medications section should reflect the post-visit medication list as a whole, with new additions flagged.'
        )
    if c.require_plan_reason:
        out.append(
            f'Plan section bullets MUST include the reason for each action -- the "why" -- using "for X" or "to X" phrasing. For example: "Order MRI to rule out disc pathology" rather than "Order MRI"; "Continue metformin for ongoing diabetes management" rather than just "Continue metformin"; "Avoid heavy lifting to prevent re-injury" rather than just "Avoid heavy lifting"; "Follow up in two weeks to reassess back pain and review MRI results" rather than just "Follow up in two weeks" or "Follow up if symptoms worsen". This applies to EVERY Plan bullet, no exceptions -- including follow-up bullets, monitoring bullets, and continue-current-treatment bullets. A trigger condition (e.g. "if symptoms worsen") is NOT a reason; the bullet must state the PURPOSE of the action (e.g. "to monitor for new neurological symptoms"). When the transcript does not explicitly state the reason, infer it from the clinical context (the {t.patient}\'s complaint, the assessment, the medication\'s indication). Do not invent reasons unrelated to the visit.'
        )
    if c.ros_distinct_from_subjective:
        out.append(
            f'Review of Systems (ROS) section: when the schema includes a "Review of Systems" field (or similarly-named system-by-system survey field like "ROS"), populate it with the {t.provider}\'s system-by-system findings from targeted questions asked during the visit -- both positives and pertinent negatives, organised by body system (HEENT, Respiratory, Cardiovascular, GI, GU, Musculoskeletal, Neuro, Skin, Psych, Constitutional, etc.). When the schema has BOTH a Subjective AND a Review of Systems field, divide the {t.patient} information between them with NO overlap: Subjective gets the {t.patient}\'s chief complaint and initial narrative (what brought them in, in their own words); Review of Systems gets the responses to the {t.provider}\'s follow-up "any chest pain? any nausea? any headaches?" survey. Subjective should not list the ROS pertinent negatives; those belong only in Review of Systems. If the {t.provider} did not ask explicit ROS questions, write "Not performed" or "Deferred" in the ROS section; do not pad ROS by restating Subjective.'
        )
    out.append(
        "Ensure all action items are included in the relevant sections of the note. But do not repeat yourself -- if a section already mentions the action item, don't mention it again."
    )
    out.append(
        f"Imitate the style and conventions that you see in the example note.  But do NOT include any of the information from the example in the note you write. This is an example taken from a DIFFERENT {t.patient}."
    )
    out.append(
        "Respond only in JSON format with the schema above. JSON property values should be valid markdown. If you include a markdown list, each item in the list should be followed by a newline character."
    )
    out.append("Take a deep breath and think step by step.")
    return out


def _instruction_block(c: NoteTypeCustomization, t: Terms = DEFAULT_TERMS) -> str:
    return "\n".join(
        f"{i + 1}. {text}" for i, text in enumerate(_ordered_instructions(c, t))
    )


# Build a structured prompt for creating a clinical note based on a transcript.
# This is structured in the sense that it expects a JSON response with specific
# fields. This may not be optimal for our usecase, but it's a good starting
# point. See https://arxiv.org/html/2408.02442v3 for more information.
def build_structured_prompt(
    transcript: AIVisitTranscript,
    example_note: DoctorNoteExample | None,
    note_schema: DoctorNoteSchema,
    customization: NoteTypeCustomization | None = None,
    *,
    action_items: str | None = None,
    doctor: Doctor | None = None,
    source_indexes: bool = False,
) -> str:
    c = customization or NoteTypeCustomization()

    transcript_text = "\n".join(
        [
            f"{u.speaker}: "
            + (f"[source {index}] " if source_indexes else "")
            + u.content
            for index, u in enumerate(transcript.transcript_data, 1)
        ]
    )

    # TODO:
    #  - give access to intake information

    doctor = doctor if doctor is not None else Doctor.one(transcript.doctor_id)
    t = terminology_terms(doctor)

    if action_items is None:
        action_items = extract_action_items(transcript, t)
    maybe_doctor_bio = "You are a medical professional.\n"
    if doctor and doctor.biography:
        maybe_doctor_bio = dedent(f"""
        You are a medical professional with the following bio:

        <bio>
        {doctor.biography.strip()}
        </bio>
        """)

    # _instruction_block returns lines at column 0. We indent the whole block to
    # 8 spaces here so that every rendered instruction line carries exactly 8
    # leading spaces, reproducing origin/master's rendered output byte-for-byte
    # (dedent() is a no-op there because interpolated transcript/schema text
    # injects column-0 lines that zero the common prefix).
    indented_block = textwrap.indent(_instruction_block(c, t), "        ")
    prompt = dedent(f"""
        {maybe_doctor_bio}

        You need to write a clinical note for a medical appointment that you just had with a {
        t.patient
    }. Your goal is to accurately summarize the key information from the appointment in a structured, professional format.

        Here is the transcript from the appointment:

        <transcript>
        {transcript_text}
        </transcript>

        Please create a clinical note based on the information provided in the transcript.
        The clinical note should have the following schema and be structured in JSON format:

        <schema>
        {json.dumps(note_schema.fields, indent=2)}
        </schema>

        {
        f'''As an example to help guide you, here is a good clinical note from another encounter.

        <example_note>
        {json.dumps(example_note.field_examples, indent=2)}
        </example_note>'''
        if example_note
        else ""
    }

        Ensure that the clinical note you create includes the following action items:

        <action_items>
        {action_items}
        </action_items>

        When creating the clinical note:

{indented_block}
    """)
    label_note = transcript_label_note(t)
    if label_note:
        prompt += "\n\n" + label_note
    if c.custom_instructions:
        prompt += (
            "\n\nAdditional provider instructions:\n" + c.custom_instructions.strip()
        )
    return prompt


def build_action_item_prompt(
    transcript: AIVisitTranscript, t: Terms = DEFAULT_TERMS
) -> str:
    transcript_text = "\n".join(
        [f"{u.speaker}: {u.content}" for u in transcript.transcript_data]
    )
    return dedent(f"""
    You are a {t.provider} reviewing the transcript of a {t.patient} visit that you conducted.
    You are looking for the actions you told the {t.patient} you would take as a result of this visit.
    You are also looking for actions that you told the {t.patient} to take.

    Actions for you might include (but not be limited to):
      - ordering labs
      - prescribing medications
      - scheduling follow-up appointments
      - writing referrals
      - consulting with other {t.provider}s
      - calling the {t.patient} to discuss lab results
      - requesting records from other providers
      - sending educational information to the {t.patient}

    Actions for the {t.patient} might include (but not be limited to):
      - taking medications
      - making lifestyle changes
      - monitoring for specific symptoms
      - looking out for changes in symptoms
      - consulting with other {t.provider}s/specialists
      - calling back with questions

    Please extract all such action items from the transcript.

    <transcript>
    {transcript_text}
    </transcript>

    Your response should be structured as a markdown list.
    """)


def extract_action_items(
    transcript: AIVisitTranscript, t: Terms = DEFAULT_TERMS
) -> str:
    prompt = build_action_item_prompt(transcript, t)
    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "system", "content": prompt}],
        temperature=0,
        top_p=0.1,
    )
    content = response.choices[0].message.content
    assert content
    return content


def generate_clinical_note(
    transcript: AIVisitTranscript,
    example_note: DoctorNoteExample | None,
    note_schema: DoctorNoteSchema,
    customization: NoteTypeCustomization | None = None,
    *,
    bot_fidelity: bool = False,
    bot_segment_boundaries: tuple[int, ...] = (),
) -> dict[str, str]:
    if bot_fidelity:
        from app.services.zoom_bot_note_grounding import generate_grounded_note

        return generate_grounded_note(
            transcript,
            example_note,
            note_schema,
            customization,
            boundaries=bot_segment_boundaries,
        )
    prompt = build_structured_prompt(
        transcript, example_note, note_schema, customization
    )

    # NB: never log the prompt itself — it contains the transcript (PHI)
    log.debug("prompting clinical note model", prompt_chars=len(prompt))

    # Note: we can't currently use structured-outputs with the chat endpoint
    # because our schema is determined at runtime. Doctors all seem to have a
    # unique note structure that they like. Tebra gives them pretty broad
    # abilities to add fields and sections to their notes and they do.
    # https://platform.openai.com/docs/guides/structured-outputs
    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )

    content = response.choices[0].message.content
    assert content

    # TODO if content schema doesn't match note_schema, we need to retry.

    # NB: never log the response itself — it is the clinical note (PHI)
    log.debug("clinical note response received", content_chars=len(content))

    return json.loads(content)


def augment_clinical_note(
    original: dict[str, str], transcript: AIVisitTranscript
) -> dict[str, str]:
    # Find key that contains the plan
    plan_key = None
    for key in original:
        if "plan" in key.lower():
            plan_key = key
            break

    if plan_key:
        # Recording duration does not establish total care time or services performed.
        original[plan_key] += "\n\n" + "This note was generated with AI assistance."

    # TODO need to add post-summarization text to the relevant sections, see:
    # https://docs.google.com/document/d/1ZOHngE8cJjRu9AP4jOw1NFfsjy9wM_EUGJNjRrD_33U/edit?tab=t.0
    return original
