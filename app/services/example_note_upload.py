import os
from dataclasses import dataclass
from typing import NoReturn

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
)

MAX_EXAMPLE_NOTE_BYTES = 10 * 1024 * 1024
PDF_CONTENT_TYPE = "application/pdf"
PDF_SIGNATURE = b"%PDF-"


@dataclass(frozen=True)
class ExampleNoteUploadValidationError(ValueError):
    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


def _invalid_pdf(
    detail: str = "Example notes must be valid PDF files.",
) -> NoReturn:
    raise ExampleNoteUploadValidationError(status_code=415, detail=detail)


def validate_example_note_blob(file_name: str, *, owner_clerk_id: str) -> None:
    """Fail closed unless an uploaded example note is a bounded PDF blob."""

    if (
        not file_name
        or file_name != os.path.basename(file_name)
        or "\\" in file_name
        or not file_name.lower().endswith(".pdf")
    ):
        _invalid_pdf("Example notes must use a .pdf file name.")
    if not owner_clerk_id or not file_name.startswith(f"{owner_clerk_id}_"):
        raise ExampleNoteUploadValidationError(
            status_code=403,
            detail="The uploaded example note does not belong to this account.",
        )

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )
    blob_client = service_client.get_blob_client(
        container=AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME,
        blob=file_name,
    )

    try:
        properties = blob_client.get_blob_properties()
    except ResourceNotFoundError as exc:
        raise ExampleNoteUploadValidationError(
            status_code=400,
            detail="The uploaded example note could not be found. Please upload it again.",
        ) from exc

    size = getattr(properties, "size", None)
    if not isinstance(size, int) or size <= 0:
        _invalid_pdf("The uploaded PDF is empty or incomplete.")
    if size > MAX_EXAMPLE_NOTE_BYTES:
        raise ExampleNoteUploadValidationError(
            status_code=413,
            detail="Example note PDFs must be 10 MB or smaller.",
        )

    content_settings = getattr(properties, "content_settings", None)
    raw_content_type = getattr(content_settings, "content_type", "") or ""
    content_type = raw_content_type.partition(";")[0].strip().lower()
    if content_type != PDF_CONTENT_TYPE:
        _invalid_pdf("Example notes must have the application/pdf content type.")

    try:
        signature = blob_client.download_blob(
            offset=0, length=len(PDF_SIGNATURE)
        ).readall()
    except ResourceNotFoundError as exc:
        raise ExampleNoteUploadValidationError(
            status_code=400,
            detail="The uploaded example note could not be found. Please upload it again.",
        ) from exc

    if signature != PDF_SIGNATURE:
        _invalid_pdf()
