# Extract the schema and content from an example clinical note.

# Unfortunately this script requires poppler to be installed on your system.
#   `brew install poppler` on MacOS
#   `apt-get install poppler-utils` on Ubuntu

import base64
import io
import json
import os
import uuid
from textwrap import dedent

import PIL.Image
from azure.storage.blob import BlobServiceClient
from decouple import config
from openai import OpenAI
from pdf2image import convert_from_path

from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
)

from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema

api_key = config("OPENAI_API_KEY", cast=str)
if not api_key:
    raise ValueError("API key not found. Please set OPENAI_API_KEY in your .env file.")
openai = OpenAI(api_key=api_key)

# TODO add logging


def extract(note_id: str):
    note = DoctorNoteFile.get(id=note_id)
    note_pages = download_note(note)

    note_fields = extract_note_fields(note_pages)
    assert note_fields
    instructions = explain_note_fields(note_fields)

    schema = DoctorNoteSchema.find_or_create_by(
        doctor_id=note.doctor_id,
        note_type=note.note_type,
        fields=instructions,
    )

    # TODO remove
    print("schema ID:", schema.id)
    print(note_fields)
    print(json.dumps(instructions, indent=2))

    content = extract_note_content(note_pages, note_fields)
    assert content

    example = DoctorNoteExample.find_or_create_by(
        doctor_id=note.doctor_id,
        doctor_note_schema_id=schema.id,
        field_examples=content,
    )

    # TODO remove
    print("doctor example ID:", example.id)
    print(json.dumps(content, indent=2))


# Convert images to base64 in a data URI format so they can be sent via API.
def get_img_uri(img: PIL.Image.Image) -> str:
    png_buffer = io.BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)
    base64_png = base64.b64encode(png_buffer.read()).decode("utf-8")
    return f"data:image/png;base64,{base64_png}"


def download_note(note: DoctorNoteFile) -> list[str]:
    filename = note.azure_file_name

    temp_download_path = f"./tmp/example-note-{uuid.uuid4()}-{filename}"
    _basename_no_ext = os.path.splitext(filename)[0]

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )
    blob_client = service_client.get_blob_client(
        container=AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
        blob=filename,
    )

    try:
        with open(temp_download_path, "wb") as file:
            file.write(blob_client.download_blob().readall())

        # https://cookbook.openai.com/examples/parse_pdf_docs_for_rag
        # Split the PDF into pages, then convert each page to an image.
        pdf_pages = convert_from_path(temp_download_path, 500)
        return [get_img_uri(page) for page in pdf_pages]

    finally:
        # Images are stored in memory at this point so we can delete the PDF
        # from disk.
        if os.path.exists(temp_download_path):
            os.remove(temp_download_path)


# Extract the fields from an example clinical note.
def extract_note_fields(note_pages: list[str]) -> list[str]:
    system_prompt = dedent("""
        You will be provided with a series of images of the pages of a PDF.
        The PDF is a clinical note created by a doctor from a patient encounter.
        What I want you to do is extract information from it.

        Here are your instructions:
        * Extract the different fields on this medical note.
        * Fields are in large font in the left column of the page and are separated by horizontal lines.
        * Don't extract any of the field values -- I don't care about the patient information.
        * Don't give any introduction or explanatory information. Just list the fields.
        * Respond only in JSON format with the following schema:

        {{
          fields: string[]
        }}
    """)

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"{data_uri}"}}
                    for data_uri in note_pages
                ],
            },
        ],
        response_format={"type": "json_object"},
        # TODO I'm not sure if this is a good temperature or top_p.
        temperature=0,
        top_p=0.1,
    )

    fields = response.choices[0].message.content
    assert fields
    return json.loads(fields)["fields"]


def explain_note_fields(fields: list[str]) -> dict[str, str]:
    prompt = dedent(f"""
        I have extracted the following fields from a clinical note:

        {fields}

        Please provide a brief instruction for each field. The instruction
        should give general guidance to another doctor on what to include in
        this portion of the note. For example:

        - field="CC", instruction="Summarize the main reason for the patient's visit in one sentence"
        - field="History of Present Illness", instruction="Provide a detailed description of the patient's current symptoms, including onset, duration, severity, and any relevant contextual information"
        - field="Assessment", instruction="List the primary diagnosis and any secondary diagnoses, along with the rationale for each diagnosis"

        Respond in JSON format such that object keys are the field names and values are their instructions.

        {{
          fieldName: instructions
        }}
    """)

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
        ],
        response_format={"type": "json_object"},
        # TODO I'm not sure if this is a good temperature or top_p.
        temperature=0,
        top_p=0.1,
    )

    # TODO We need to ensure that the model sends back all of the fields and
    # doesn't hallucinate any new ones.

    instructions = response.choices[0].message.content
    assert instructions
    return json.loads(instructions)


def extract_note_content(note_pages: list[str], fields: list[str]) -> dict[str, str]:
    system_prompt = dedent("""
        You will be provided with a series of images of the pages of a PDF.
        The PDF is a clinical note created by a doctor from a patient encounter.
        What I want you to do is extract information from it.
        You will also be provided with a list of fields in the clinical note. Extract the content for each field.

        Fields to extract:
        {fields}

        Here are your instructions:
        * Extract the content in the note for each of the fields provided.
        * Fields are in large font in the left column of the page and are separated by horizontal lines.
        * Field content is in smaller font in the right column of the page.
        * Content can span multiple pages of the PDF.
        * Don't give any introduction or explanatory information. Just list the field content.
        * Respond only in JSON format such that object keys are the field names and values are their associated content.

        {{
          fieldName: content
        }}
    """)

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"{data_uri}"}}
                    for data_uri in note_pages
                ],
            },
        ],
        response_format={"type": "json_object"},
        # TODO I'm not sure if this is a good temperature or top_p.
        temperature=0,
        top_p=0.1,
    )

    content = response.choices[0].message.content
    assert content

    # TODO the field examples need to be deidentified with surrogates

    # TODO We need to ensure that the model sends back all of the fields and
    # doesn't hallucinate any new ones.

    return json.loads(content)
