import json
import math
from textwrap import dedent

from app import log
from app.celery import celery
from app.configuration.openai import openai

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema

OPENAI_MODEL = "gpt-4o"


def perform(transcript_id: str, schema_id: str):
    """
    Summarize a visit transcript into a clinical note.
    """
    log.info("starting summarization", transcript_id=transcript_id, schema_id=schema_id)

    transcript = AIVisitTranscript.get(id=transcript_id)
    assert transcript

    schema = DoctorNoteSchema.get(id=schema_id)
    assert schema

    existing_note = AIVisitNote.get(
        AIVisitNote.ai_transcript_id == transcript.id and
        AIVisitNote.doctor_note_schema_id == schema.id
    )
    if existing_note:
        log.info("note already exists, skipping summarization",
                 transcript_id=transcript_id, schema_id=schema_id)
        return

    # Get most recently updated example note for this schema
    example = (
        DoctorNoteExample.select()
        .where(DoctorNoteExample.doctor_note_schema_id == schema_id)
        # TODO is there a way to actually fix this type? Feels like a limitation on sqlmodel
        .order_by(DoctorNoteExample.updated_at.desc())
        .limit(1)
        .one()
    )

    # TODO Validate the example note matches the schema.

    log.info("generating clinical note")
    note = generate_clinical_note(
        transcript=transcript,
        example_note=example,
        note_schema=schema,
    )

    log.info("augmenting clinical note")
    note = augment_clinical_note(original=note, transcript=transcript)

    appointment = Appointment.get(id=transcript.appointment_id)
    assert appointment

    fallback_name = "General Appointment"
    # primary compliant / condition
    appointment.appointment_name = note.get("CC", fallback_name)
    # TODO can we add an appointment summary here? Maybe a separate openai call?
    appointment.save()

    log.info("saving visit note")
    AIVisitNote.find_or_create_by(
        doctor_id=transcript.doctor_id,
        appointment_id=transcript.appointment_id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note=note,
    )


perform_celery = celery.task(perform)


def queue(*args, **kwargs):
    log.info("queuing summarization job", args=args, kwargs=kwargs)
    perform_celery.delay(*args, **kwargs)


# Build a structured prompt for creating a clinical note based on a transcript.
# This is structured in the sense that it expects a JSON response with specific
# fields. This may not be optimal for our usecase, but it's a good starting
# point. See https://arxiv.org/html/2408.02442v3 for more information.
def build_structured_prompt(
    transcript: AIVisitTranscript,
    example_note: DoctorNoteExample,
    note_schema: DoctorNoteSchema,
) -> str:
    transcript_text = "\n".join(
        [f"{u.speaker}: {u.content}" for u in transcript.transcript_data]
    )

    action_items = extract_action_items(transcript)

    # TODO:
    #  - give access to intake information
    #  - instruct model that each field should be formatted as markdown

    return dedent(f"""
        You are a medical professional tasked with creating a clinical note based on a transcript from a medical encounter. Your goal is to accurately summarize the key information from the encounter in a structured, professional format.

        Here is the transcript from the encounter:

        <transcript>
        {transcript_text}
        </transcript>

        Please create a clinical note based on the information provided in the transcript.
        The clinical note should have the following schema and be structured in JSON format:

        <schema>
        {json.dumps(note_schema.fields, indent=2)}
        </schema>

        As an example to help guide you, here is a good clinical note from another encounter.

        <example_note>
        {json.dumps(example_note.field_examples, indent=2)}
        </example_note>

        Ensure that the clinical note you create includes the following action items:

        <action_items>
        {action_items}
        </action_items>

        When creating the clinical note:

        1. Carefully review the transcript and extract all relevant medical information.
        2. Organize the information into the appropriate sections of the clinical note schema.
        3. Use clear, concise, and professional medical language.
        4. Avoid including any irrelevant personal details or small talk from the transcript.
        5. If certain information is not provided in the transcript, leave that section blank or write "Not mentioned in transcript" as appropriate. E.g. if the patient does not mention respiratory problems, do NOT assume that they do not have them.
        6. Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information.
        7. Ensure all action items are included in the relevant sections of the note.
        8. Imitate the style and conventions that you see in the example note.  But do NOT include any of the information from the example in the note you write. This is an example taken from a DIFFERENT patient.
        9. Take a deep breath and think step by step.

        Respond only in JSON format with the schema above.
    """)

def build_action_item_prompt(transcript: AIVisitTranscript) -> str:
    transcript_text = "\n".join(
        [f"{u.speaker}: {u.content}" for u in transcript.transcript_data]
    )
    return dedent(f"""
    You are a doctor reviewing the transcript of a patient visit that you conducted.
    You are looking for the actions you told the patient you would take as a result of this visit.

    Actions might include (but not be limited to)::
      - ordering labs
      - prescribing medications
      - scheduling follow-up appointments
      - writing referrals
      - consulting with other doctors
      - calling the patient to discuss lab results
      - requesting records from other providers
      - sending educational information to the patient

    Please extract all such action items from the transcript.

    <transcript>
    {transcript_text}
    </transcript>

    Your response should be structured as a markdown list.
    """)

def extract_action_items(transcript: AIVisitTranscript) -> str:
    prompt = build_action_item_prompt(transcript)
    response = openai.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": prompt}],
        temperature=0,
        top_p=0.1,
    )
    content = response.choices[0].message.content
    assert content
    return content

def generate_clinical_note(
    transcript: AIVisitTranscript,
    example_note: DoctorNoteExample,
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
        model=OPENAI_MODEL,
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
