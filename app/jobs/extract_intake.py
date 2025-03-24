"""
Extract the schema and content from an example intake form.

Requires poppler to be installed on your system.
"""

import base64
import io
import json
import os
import uuid
from textwrap import dedent

import PIL.Image
from azure.storage.blob import BlobServiceClient
from pdf2image import convert_from_path
from typeid import TypeID

from app import log
from app.celery import celery
from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_EXAMPLE_INTAKE_CONTAINER_NAME,
)
from app.configuration.openai import openai

from activemodel.types import TypeIDType
from app.models.doctor_note_example import DoctorNoteExample
from app.models.intake.doctor_intake_file import DoctorIntakeFile
from app.models.intake.doctor_intake_schema import DoctorIntakeSchema, IntakeQuestion

OPENAI_MODEL = "gpt-4o"

def perform(doctor_intake_file_id: str | TypeID | TypeIDType):
    """
    Extract a schema and content from an example intake form.
    """

    log.info("extracting intake questions", intake_id=doctor_intake_file_id)

    file = DoctorIntakeFile.one(id=doctor_intake_file_id)

    intake_pages = download_intake_file(file)

    questions = extract_intake_questions(intake_pages)
    assert questions

    schema = DoctorIntakeSchema(
        doctor_id=file.doctor_id,
        note_type=file.form_type,
        questions=questions,
    ).save()


perform_celery = celery.task(perform)


# TODO there may be some fancy arg parsing we can do here? https://stackoverflow.com/questions/73856901/how-can-i-use-paramspec-with-method-decorators
def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)


# Convert images to base64 in a data URI format so they can be sent via API.
def get_img_uri(img: PIL.Image.Image) -> str:
    png_buffer = io.BytesIO()
    img.save(png_buffer, format="PNG")
    png_buffer.seek(0)
    base64_png = base64.b64encode(png_buffer.read()).decode("utf-8")
    return f"data:image/png;base64,{base64_png}"


def download_intake_file(file: DoctorIntakeFile) -> list[str]:
    full_upload_url = file.azure_file_name

    # TODO throw if it isn't a pdf?
    if not full_upload_url.endswith(".pdf"):
        raise ValueError("File is not a PDF")

    # We store the full upload url from azure in the DB, so we extract just the
    # last path component here.
    filename = os.path.basename(full_upload_url)

    temp_download_path = f"./tmp/example-intake-{uuid.uuid4()}-{filename}"

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )
    blob_client = service_client.get_blob_client(
        container=AZURE_UPLOADS_EXAMPLE_INTAKE_CONTAINER_NAME,
        blob=filename,
    )

    try:
        with open(temp_download_path, "wb") as f:
            f.write(blob_client.download_blob().readall())

        # https://cookbook.openai.com/examples/parse_pdf_docs_for_rag
        # Split the PDF into pages, then convert each page to an image.
        pdf_pages = convert_from_path(temp_download_path, 500)
        return [get_img_uri(page) for page in pdf_pages]

    finally:
        # Images are stored in memory at this point so we can delete the PDF
        # from disk.
        if os.path.exists(temp_download_path):
            os.remove(temp_download_path)


# Extract the questions from a clinical intake form.
# `intake_pages` is a list of base64-encoded images of the pages of the form PDF.
def extract_intake_questions(intake_pages: list[str]) -> list[str]:
    question_schema = IntakeQuestion.model_json_schema()["properties"]
    question_schema.pop("key")
    question_schema.pop("description")

    system_prompt = dedent("""
        You will be provided with a series of images of the pages of a PDF.
        The PDF is a clinical intake form created by a doctor.
        The form is intended to be filled out by patients prior to their appointments.
        I want you to extract information from this form.

        Here are your instructions:
        - Determine what pieces of patient information the doctor is asking for in the form.
        - A lot of the information will be requested in the form of questions, but not all.
        - Requests for information can take the form of commands, e.g. "Please provide the date of your last appointment"
          - Rephrase these as questions, e.g. "What was the date of your last appointment?"
        - Take note of the response type that the doctor is asking for.
          - Questions may be multiple choice:
            - e.g. "How often does your back hurt?" with options "Never", "Sometimes", "Always"
            - e.g. "Do you have any allergies?" with options "Yes", "No"
          - Questions may allow free-form responses:
            - e.g. "Is there anything else you would like to tell us?" with a large box for the patient to write in.
        - Some questions may depend on the answers to previous questions.
          - When this is the case, you may condense the information into a single question.
          - e.g. "Have you had any falls?" with a follow-up question "If yes, please describe the fall" may be condensed into "If you have had any falls, can you please describe them?"
        - You may ignore administrative questions, e.g.:
          - "What is your name?"
          - "What is your date of birth?"
          - "What is today's date?"
        - Respond only in JSON format with the following SCHEMA:

        interface Question {json.dumps(question_schema, indent=2)}

        <SCHEMA>
        {{
          questions: Question[]
        }}
        </SCHEMA>
    """)

    response = openai.chat.completions.create(
        model=OPENAI_MODEL,
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

    questions = response.choices[0].message.content
    assert questions
    return json.loads(questions)["questions"]
