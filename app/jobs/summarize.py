import json
import math
from textwrap import dedent

from decouple import config
from openai import OpenAI

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema

api_key = config("OPENAI_API_KEY", cast=str)
if not api_key:
    raise ValueError("API key not found. Please set OPENAI_API_KEY in your .env file.")
openai = OpenAI(api_key=api_key)

# TODO add logging


def summarize(transcript_id: str, example_note_id: str):
    transcript = AIVisitTranscript.get(id=transcript_id)
    assert transcript
    example = DoctorNoteExample.get(id=example_note_id)
    assert example
    schema = DoctorNoteSchema.get(id=example.doctor_note_schema_id)
    assert schema

    # TODO Validate the example note matches the schema.

    note = generate_clinical_note(
        transcript=transcript,
        example_note=example,
        note_schema=schema,
    )

    note = augment_clinical_note(original=note, transcript=transcript)

    appointment = Appointment.get(id=transcript.appointment_id)
    assert appointment
    fallback_name = f"{appointment.appointment_date} Appointment"
    appointment.appointment_name = note.get("CC", fallback_name)
    appointment.save()

    AIVisitNote.find_or_create_by(
        doctor_id=transcript.doctor_id,
        appointment_id=transcript.appointment_id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note=note,
    )


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
        [f"{u['speaker']}: {u['content']}" for u in transcript.transcript_data]
    )

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

        When creating the clinical note:

        1. Carefully review the transcript and extract all relevant medical information.
        2. Organize the information into the appropriate sections of the clinical note schema.
        3. Use clear, concise, and professional medical language.
        4. Avoid including any irrelevant personal details or small talk from the transcript.
        5. If certain information is not provided in the transcript, leave that section blank or write "Not mentioned in transcript" as appropriate. E.g. if the patient does not mention respiratory problems, do NOT assume that they do not have them.
        6. Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information.
        7. Imitate the style and conventions that you see in the example note.  But do NOT include any of the information from the example in the note you write. This is an example taken from a DIFFERENT patient.
        8. Take a deep breath and think step by step.

        Respond only in JSON format with the schema above.
    """)


def generate_clinical_note(
    transcript: AIVisitTranscript,
    example_note: DoctorNoteExample,
    note_schema: DoctorNoteSchema,
) -> dict[str, str]:
    prompt = build_structured_prompt(transcript, example_note, note_schema)
    print("Prompting 4o with:")
    print(prompt)
    # Note: we can't currently use structured-outputs with the chat endpoint
    # because our schema is determined at runtime. Doctors all seem to have a
    # unique note structure that they like. Tebra gives them pretty broad
    # abilities to add fields and sections to their notes and they do.
    # https://platform.openai.com/docs/guides/structured-outputs
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        top_p=0.1,
    )

    content = response.choices[0].message.content
    assert content

    # TODO if content schema doesn't match note_schema, we need to retry.

    print("Response:")
    print(content)

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
