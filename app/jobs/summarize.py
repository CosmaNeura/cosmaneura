"""
Summarize a patient visit into a clinical note with the specified schema.
"""

import json
import math
from textwrap import dedent
from typing import Any, cast

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai

from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import Intake


def perform(transcript_id: str | TypeIDType, schema_id: str | TypeIDType):
    log.info("starting summarization", transcript_id=transcript_id, schema_id=schema_id)

    trigger = AIVisitTranscript.get(id=transcript_id)
    assert trigger

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
                AIVisitTranscript.appointment_id == trigger.appointment_id
            )
            .order_by(cast(Any, AIVisitTranscript.created_at).asc())
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
    )
    if existing_note and existing_note.ai_transcript_id == latest.id:
        log.info(
            "note already reflects latest transcript, skipping summarization",
            appointment_id=str(trigger.appointment_id),
            schema_id=schema_id,
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

    log.info(
        "generating clinical note",
        segment_count=len(all_transcripts),
        merged_duration=merged.duration,
    )
    note = generate_clinical_note(
        transcript=merged,
        example_note=example,
        note_schema=schema,
    )

    log.info("augmenting clinical note")
    note = augment_clinical_note(original=note, transcript=merged)

    appointment = Appointment.one(trigger.appointment_id)

    # CC == cheif complaint.
    appointment_name = note.get("CC", "General Appointment")

    # TODO unclear if we definitely want the intake appointment name to always override the appointment_name above
    # intake will not always exist, but if it does, let's pull the appointment name
    intake = Intake.get(appointment_id=appointment.id)
    if intake and intake.cleaned_external_intake_data:
        data = intake.cleaned_external_intake_data
        appointment_name = f"{data.appointment_type} -- {data.reason_for_visit}"

    appointment.appointment_name = appointment_name
    # TODO can we add an appointment summary here? Maybe a separate openai call?
    appointment.save()

    # Replace any stale note for this (appointment, schema). We hard-delete
    # because AIVisitNote has no soft-deletion mixin and the previous note
    # was generated from a now-incomplete subset of segments.
    if existing_note:
        existing_note.delete()

    log.info("saving visit note")
    AIVisitNote(
        doctor_id=trigger.doctor_id,
        appointment_id=trigger.appointment_id,
        ai_transcript_id=latest.id,
        doctor_note_schema_id=schema.id,
        note=note,
    ).save()


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


def queue(*args, **kwargs):
    log.info("queuing summarization job", args=args, kwargs=kwargs)
    perform_celery.delay(*args, **kwargs)


# Build a structured prompt for creating a clinical note based on a transcript.
# This is structured in the sense that it expects a JSON response with specific
# fields. This may not be optimal for our usecase, but it's a good starting
# point. See https://arxiv.org/html/2408.02442v3 for more information.
def build_structured_prompt(
    transcript: AIVisitTranscript,
    example_note: DoctorNoteExample | None,
    note_schema: DoctorNoteSchema,
) -> str:
    transcript_text = "\n".join(
        [f"{u.speaker}: {u.content}" for u in transcript.transcript_data]
    )

    action_items = extract_action_items(transcript)

    # TODO:
    #  - give access to intake information

    doctor = Doctor.one(transcript.doctor_id)
    maybe_doctor_bio = "You are a medical professional.\n"
    if doctor and doctor.biography:
        maybe_doctor_bio = dedent(f"""
        You are a medical professional with the following bio:

        <bio>
        {doctor.biography.strip()}
        </bio>
        """)

    return dedent(f"""
        {maybe_doctor_bio}

        You need to write a clinical note for a medical appointment that you just had with a patient. Your goal is to accurately summarize the key information from the appointment in a structured, professional format.

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

        1. Keep in mind your BIO and the kind of visit that the patient is coming in for. This will determine what information is most important to include in your note.
        2. Carefully review the transcript and extract all relevant medical information.
        3. Organize the information into the appropriate sections of the clinical note schema.
          - Within each section, you should group information based on condition.
          - E.g. if the patient discusses diabetes and hypertension, and 3 steps in the plan relate to diabetes and 2 steps relate to hypertension, the plan should list the 3 diabetes steps, THEN the 2 hyptertension steps.
          - Sort based on importance of the condition. If hypertension is the patient's main concern, it should be listed first.
          - Be consistent with the order of conditions across fields. I.e. if anxiety is covered first in the plan, it should also be covered first in the assessment, subjective, and objective sections.
          - Do not create subsections within the fields for the conditions. Prefer "flat" lists of items unless absolutely necessary.
        4. Use clear, concise, and professional medical language.
        5. Avoid including any irrelevant personal details or small talk from the transcript.
        6. If certain information is not provided in the transcript, leave that section blank or write "Not mentioned in transcript" as appropriate. E.g. if the patient does not mention respiratory problems, do NOT assume that they do not have them.
        7. Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information.
        8. Pronouns: When the transcript reveals the patient's pronouns -- via self-identification, the doctor's use of "she/her" or "he/him" when referring to the patient, or other unambiguous context -- the note MUST use those pronouns when referring to the patient. Do not default to "the patient" everywhere when pronouns are clear from the transcript. Use "they/them" ONLY when no pronoun usage is evident anywhere in the transcript. This overrides the clinical convention of always saying "the patient"; the example note may use "the patient" universally but you should mix in the patient's actual pronouns when they are known.
        9. Subjective section: source ONLY from lines spoken by the patient (lines beginning with "Patient:" in the transcript above). The doctor's interpretations, diagnostic impressions, follow-up questions, and clinical observations belong in the Assessment or Objective sections, never in Subjective. Phrasings like "the patient reports X" or "the patient denies Y" are acceptable as long as X and Y themselves came from a Patient: line.
        10. Ensure all action items are included in the relevant sections of the note. But do not repeat yourself -- if a section already mentions the action item, don't mention it again.
        11. Imitate the style and conventions that you see in the example note.  But do NOT include any of the information from the example in the note you write. This is an example taken from a DIFFERENT patient.
        12. Respond only in JSON format with the schema above. JSON property values should be valid markdown. If you include a markdown list, each item in the list should be followed by a newline character.
        13. Take a deep breath and think step by step.
    """)


def build_action_item_prompt(transcript: AIVisitTranscript) -> str:
    transcript_text = "\n".join(
        [f"{u.speaker}: {u.content}" for u in transcript.transcript_data]
    )
    return dedent(f"""
    You are a doctor reviewing the transcript of a patient visit that you conducted.
    You are looking for the actions you told the patient you would take as a result of this visit.
    You are also looking for actions that you told the patient to take.

    Actions for you might include (but not be limited to):
      - ordering labs
      - prescribing medications
      - scheduling follow-up appointments
      - writing referrals
      - consulting with other doctors
      - calling the patient to discuss lab results
      - requesting records from other providers
      - sending educational information to the patient

    Actions for the patient might include (but not be limited to):
      - taking medications
      - making lifestyle changes
      - monitoring for specific symptoms
      - looking out for changes in symptoms
      - consulting with other doctors/specialists
      - calling back with questions

    Please extract all such action items from the transcript.

    <transcript>
    {transcript_text}
    </transcript>

    Your response should be structured as a markdown list.
    """)


def extract_action_items(transcript: AIVisitTranscript) -> str:
    prompt = build_action_item_prompt(transcript)
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
) -> dict[str, str]:
    prompt = build_structured_prompt(transcript, example_note, note_schema)

    log.debug("prompting 4o with", prompt=prompt)

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

    log.debug("response", content=content)

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
        original[plan_key] += "\n\n" + dedent(f"""
        Greater than {math.floor((transcript.duration or 0) / 60)} minutes was spent in patient care, including review of prior records, consultation with other providers, and documentation.
        """)

        original[plan_key] += "\n\n" + "This note was generated with AI assistance."

    # TODO need to add post-summarization text to the relevant sections, see:
    # https://docs.google.com/document/d/1ZOHngE8cJjRu9AP4jOw1NFfsjy9wM_EUGJNjRrD_33U/edit?tab=t.0
    return original
