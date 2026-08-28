"""
Extract the schema and content from an example clinical note.

Requires poppler to be installed on your system.
"""

import base64
import io
import json
import tempfile
from collections.abc import Sized
from pathlib import Path
from textwrap import dedent

import PIL.Image
from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFPopplerTimeoutError,
    PDFSyntaxError,
)
from typeid import TypeID

from app import log
from app.celery import celery_app
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai
from app.services.doctor_access import doctor_capability_access_in_force
from app.services.example_note_upload import (
    ExampleNoteBlobIntegrityError,
    download_registered_or_legacy_example_note,
)
from app.services.external_webhook_inbox import provider_log_ref

from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema

from .anonymize import deidentify

_MAX_LOG_COUNT = 10_000
_MAX_EXAMPLE_NOTE_PAGES = 20
_PDF_INFO_TIMEOUT_SECONDS = 15
_PDF_RENDER_TIMEOUT_SECONDS = 60
_MAX_RENDER_DIMENSION = 2_500


def _bounded_count(items: Sized) -> int:
    return min(len(items), _MAX_LOG_COUNT)


def perform(doctor_note_file_id: str | TypeID | TypeIDType):
    """
    Extract a schema and content from an example clinical note.

    If a schema for the note type already exists, we currently do NOT extract additional fields from the note.
    """

    note_ref = provider_log_ref(f"note-file:{doctor_note_file_id}")
    log.info(
        "clinical note extraction started",
        event_code="note_extraction_started",
        note_ref=note_ref,
    )

    note = DoctorNoteFile.one(id=doctor_note_file_id)
    doctor_ref = provider_log_ref(f"doctor:{note.doctor_id}")
    doctor = Doctor.get(id=note.doctor_id)
    if doctor is None or not doctor_capability_access_in_force(doctor, "soap_note"):
        log.info(
            "clinical note extraction skipped: access unavailable",
            event_code="note_extraction_access_not_in_force",
            note_ref=note_ref,
            doctor_ref=doctor_ref,
        )
        return

    note_pages = download_note(note)

    note_fields = extract_note_fields(note_pages)
    assert note_fields

    # there can (and should!) be multiple examples per schema, so find a schema by the type
    # TODO given that we are searching by fields, this will definitely cause a conflict, need to think this through
    schema = DoctorNoteSchema.find_or_initialize_by(
        doctor_id=note.doctor_id,
        note_type=note.note_type,
    )

    if schema.is_new():
        instructions = explain_note_fields(note_fields)
        log.info(
            "clinical note schema extracted",
            event_code="note_schema_extracted",
            note_ref=note_ref,
            doctor_ref=doctor_ref,
            field_count=_bounded_count(instructions),
        )

        schema.fields = instructions
        schema.save()

    log.debug(
        "clinical note content extraction started",
        event_code="note_content_extraction_started",
        note_ref=note_ref,
        doctor_ref=doctor_ref,
        schema_ref=provider_log_ref(f"note-schema:{schema.id}"),
        field_count=_bounded_count(note_fields),
    )

    content = extract_note_content(note_pages, note_fields)
    assert content

    example = DoctorNoteExample.find_or_create_by(
        doctor_id=note.doctor_id,
        doctor_note_schema_id=schema.id,
        field_examples=content,
    )

    log.info(
        "clinical note extraction completed",
        event_code="note_extraction_completed",
        note_ref=note_ref,
        doctor_ref=doctor_ref,
        example_ref=provider_log_ref(f"note-example:{example.id}"),
        field_count=_bounded_count(content),
    )


perform_celery = celery_app.task(perform)


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


def download_note(note: DoctorNoteFile) -> list[str]:
    content = download_registered_or_legacy_example_note(
        note.azure_file_name,
        doctor_id=str(note.doctor_id),
    )
    temp_download_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="example-note-",
            suffix=".pdf",
            dir="./tmp",
            delete=False,
        ) as file:
            file.write(content)
            temp_download_path = file.name

        try:
            page_count = pdfinfo_from_path(
                temp_download_path,
                timeout=_PDF_INFO_TIMEOUT_SECONDS,
            ).get("Pages")
            if (
                not isinstance(page_count, int)
                or not 0 < page_count <= _MAX_EXAMPLE_NOTE_PAGES
            ):
                raise ExampleNoteBlobIntegrityError(
                    "example_note_pdf_page_limit_exceeded"
                )

            # https://cookbook.openai.com/examples/parse_pdf_docs_for_rag
            # Bound page count, dimensions, concurrency, and parser time before
            # any image is handed to the model.
            pdf_pages = convert_from_path(
                temp_download_path,
                dpi=500,
                first_page=1,
                last_page=page_count,
                thread_count=1,
                strict=True,
                size=_MAX_RENDER_DIMENSION,
                timeout=_PDF_RENDER_TIMEOUT_SECONDS,
            )
        except (
            PDFInfoNotInstalledError,
            PDFPageCountError,
            PDFPopplerTimeoutError,
            PDFSyntaxError,
        ):
            raise ExampleNoteBlobIntegrityError(
                "example_note_pdf_processing_failed"
            ) from None
        if len(pdf_pages) != page_count:
            raise ExampleNoteBlobIntegrityError("example_note_pdf_processing_failed")
        return [get_img_uri(page) for page in pdf_pages]

    finally:
        # Images are stored in memory at this point so we can delete the PDF
        # from disk.
        if temp_download_path is not None:
            Path(temp_download_path).unlink(missing_ok=True)


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
        model=DEFAULT_MULTI_MODAL_MODEL,
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
        model=DEFAULT_MULTI_MODAL_MODEL,
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
    system_prompt = dedent(f"""
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
        model=DEFAULT_MULTI_MODAL_MODEL,
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

    note = json.loads(content)

    # TODO We need to validate that the model sends back all of the fields and
    # doesn't hallucinate any new ones.

    # The field examples need to be deidentified with surrogates.
    for field in note.keys():
        note[field] = deidentify(note[field])

    return note
