"""
Generate a short pre-session briefing for a provider.

Brevity is the core design constraint: the whole brief must be readable in
well under a minute (3-5 talking points, hard word budget). This is what
distinguishes it from the intake summary, which is a fuller ~250-word read.

The brief's content is PHI — log IDs and status only, never content.
"""

import json
import os
from textwrap import dedent

from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.services.patient_context import PatientContext, assemble_patient_context

from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.session_prep import SessionPrep, SessionPrepStatus

MAX_TALKING_POINTS = 5
MAX_OPEN_THREADS = 3
MAX_ALERTS = 3
MAX_TOTAL_WORDS = 120
"""
Half the intake summary's 250-word budget. The pilot feedback that motivated
this feature was that ad-hoc LLM prep output is too long to review before a
session — do not raise this without revisiting that constraint.
"""


def should_use_test_openai_fallback() -> bool:
    return os.environ.get("PYTHON_ENV") == "test" and not (
        os.environ.get("AZURE_OPENAI_API_KEY")
        and os.environ.get("AZURE_OPENAI_ENDPOINT")
    )


def _local_generate_session_prep(context: PatientContext) -> dict:
    "deterministic offline stand-in for tests, mirroring the intake jobs' fallbacks"

    talking_points: list[str] = []
    open_threads: list[str] = []

    if context.intake_summary:
        talking_points.append("Review intake responses before the session")

    for note_context in context.notes[:2]:
        plan = note_context.note.get("Plan")
        if plan:
            talking_points.append(
                f"Follow up on prior plan ({note_context.days_ago}d ago)"
            )

    if context.notes:
        first_plan = context.notes[0].note.get("Plan")
        if first_plan:
            open_threads.append(first_plan[:80])

    if not talking_points:
        talking_points = ["First session: no prior history in Cosmas"]

    return {
        "talking_points": talking_points[:MAX_TALKING_POINTS],
        "open_threads": open_threads[:MAX_OPEN_THREADS],
        "alerts": [],
        "personality_note": (
            context.engagement_role.role_name if context.engagement_role else None
        ),
    }


def perform(session_prep_id: str | TypeID | TypeIDType):
    log.info("generating session prep", session_prep_id=session_prep_id)

    session_prep = SessionPrep.one(id=session_prep_id)

    if session_prep.status == SessionPrepStatus.completed:
        log.info(
            "session prep already completed, skipping", session_prep_id=session_prep_id
        )
        return

    context = assemble_patient_context(session_prep.doctor_id, session_prep.patient_id)
    doctor = Doctor.one(id=session_prep.doctor_id)

    content = generate_session_prep(doctor, context)

    if content is None:
        session_prep.status = SessionPrepStatus.failed
        session_prep.save()
        log.error("failed to generate session prep", session_prep_id=session_prep_id)
        return

    session_prep.talking_points = content["talking_points"][:MAX_TALKING_POINTS]
    session_prep.open_threads = content["open_threads"][:MAX_OPEN_THREADS]
    session_prep.alerts = content["alerts"][:MAX_ALERTS]
    session_prep.personality_note = content["personality_note"]
    session_prep.days_since_last_session = (
        context.notes[0].days_ago if context.notes else None
    )
    session_prep.visits_in_context = len(context.notes)
    session_prep.model = (
        None if should_use_test_openai_fallback() else DEFAULT_MULTI_MODAL_MODEL
    )
    session_prep.status = SessionPrepStatus.completed
    session_prep.save()

    log.info("generated session prep", session_prep_id=session_prep_id)


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)


def build_session_prep_prompt(doctor: Doctor, context: PatientContext) -> str:
    notes_section = ""
    for note_context in context.notes:
        notes_section += dedent(f"""
        <NOTE days_ago="{note_context.days_ago}">
        {json.dumps(note_context.note, indent=2)}
        </NOTE>
        """)

    intake_section = ""
    if context.intake_summary:
        intake_section = dedent(f"""
        The patient's most recent intake summary:

        <INTAKE_SUMMARY>
        {context.intake_summary}
        </INTAKE_SUMMARY>
        """)

    engagement_section = ""
    if context.engagement_role:
        engagement_section = dedent(f"""
        A separate system has read the patient's communication style from prior
        sessions as: {context.engagement_role.role_name}.
        """)

    return dedent(f"""
    You are a doctor with the following bio:

    <BIO>
    {doctor.biography}
    </BIO>

    You are about to see a patient you have seen before. Here are the clinical
    notes from their most recent visits, newest first:

    {notes_section}

    {intake_section}

    {engagement_section}

    Produce a pre-session brief you will scan in the seconds before the
    session starts. Respond only in JSON with the following SCHEMA:

    <SCHEMA>
    {{
      "talking_points": string[],   // 3-5 items, each 15 words or fewer
      "open_threads": string[],     // 0-{MAX_OPEN_THREADS} unresolved items from the MOST RECENT note only (plan items, assignments given to the patient)
      "alerts": string[],           // 0-{MAX_ALERTS} items; ONLY safety/risk mentions (e.g. self-harm, suicidal ideation, abuse) or medication changes found in the notes. Empty array if none — do NOT invent alerts.
      "personality_note": string | null  // one clause (12 words max) on how this patient prefers to communicate/decide, ONLY if stated above; otherwise null
    }}
    </SCHEMA>

    Rules:
    1. The ENTIRE brief must total {MAX_TOTAL_WORDS} words or fewer. Brevity beats completeness.
    2. Be specific: name the actual condition, medication, or event — never "discuss progress".
    3. Do not repeat the same fact in two sections.
    4. Do not include any intro, conclusion, or commentary — JSON only.
    """)


def generate_session_prep(doctor: Doctor, context: PatientContext) -> dict | None:
    if should_use_test_openai_fallback():
        return _local_generate_session_prep(context)

    prompt = build_session_prep_prompt(doctor, context)

    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )

    raw = response.choices[0].message.content
    if not raw:
        return None

    content = json.loads(raw)

    if (
        not isinstance(content.get("talking_points"), list)
        or not content["talking_points"]
    ):
        return None

    return {
        "talking_points": [str(point) for point in content["talking_points"]],
        "open_threads": [str(thread) for thread in content.get("open_threads") or []],
        "alerts": [str(alert) for alert in content.get("alerts") or []],
        "personality_note": (
            str(content["personality_note"])
            if content.get("personality_note")
            else None
        ),
    }
