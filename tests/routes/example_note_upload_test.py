from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import status
from fastapi.testclient import TestClient

import app.jobs.extract
import app.services.example_note_upload as example_note_upload
from app.server import api_app

from app.models.doctor_note_file import DoctorNoteFile

from tests.routes.utils import get_clerk_dev_user


class _FakeDownload:
    def __init__(self, content: bytes):
        self.content = content

    def readall(self) -> bytes:
        return self.content


class _FakeBlobClient:
    def __init__(self, *, content_type: str, content: bytes, size: int | None = None):
        self.content_type = content_type
        self.content = content
        self.size = len(content) if size is None else size

    def get_blob_properties(self):
        return SimpleNamespace(
            size=self.size,
            content_settings=SimpleNamespace(content_type=self.content_type),
        )

    def download_blob(self, *, offset: int, length: int):
        return _FakeDownload(self.content[offset : offset + length])


def _install_blob(
    monkeypatch, *, content_type: str, content: bytes, size: int | None = None
) -> None:
    blob_client = _FakeBlobClient(
        content_type=content_type,
        content=content,
        size=size,
    )
    service_client = SimpleNamespace(
        get_blob_client=lambda **_kwargs: blob_client,
    )
    monkeypatch.setattr(
        example_note_upload,
        "BlobServiceClient",
        lambda *_args, **_kwargs: service_client,
    )


def _register_example_note(client: TestClient, file_name: str):
    _, _, user = get_clerk_dev_user()
    return client.post(
        api_app.url_path_for("upload_example_note"),
        params={
            "file_name": f"{user.id}_1234567890_{file_name}",
            "note_type": "SOAP Note",
        },
    )


def test_rejects_an_image_before_persisting_or_queueing(
    authenticated_client: TestClient, monkeypatch
):
    _install_blob(
        monkeypatch,
        content_type="image/jpeg",
        content=b"\xff\xd8\xff\xe0fake-image",
    )
    queue = Mock()
    monkeypatch.setattr(app.jobs.extract, "queue", queue)
    initial_count = DoctorNoteFile.count()

    response = _register_example_note(authenticated_client, "renamed-image.pdf")

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert response.json()["detail"] == (
        "Example notes must have the application/pdf content type."
    )
    assert DoctorNoteFile.count() == initial_count
    queue.assert_not_called()


def test_rejects_invalid_pdf_signature_before_persisting_or_queueing(
    authenticated_client: TestClient, monkeypatch
):
    _install_blob(
        monkeypatch,
        content_type="application/pdf",
        content=b"not-a-pdf",
    )
    queue = Mock()
    monkeypatch.setattr(app.jobs.extract, "queue", queue)
    initial_count = DoctorNoteFile.count()

    response = _register_example_note(authenticated_client, "invalid.pdf")

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert response.json()["detail"] == "Example notes must be valid PDF files."
    assert DoctorNoteFile.count() == initial_count
    queue.assert_not_called()


def test_accepts_bounded_pdf_then_persists_and_queues(
    authenticated_client: TestClient, monkeypatch
):
    _install_blob(
        monkeypatch,
        content_type="application/pdf",
        content=b"%PDF-1.7\nsynthetic test document",
    )
    queue = Mock()
    monkeypatch.setattr(app.jobs.extract, "queue", queue)
    initial_count = DoctorNoteFile.count()

    response = _register_example_note(authenticated_client, "valid.pdf")

    assert response.status_code == status.HTTP_200_OK
    assert DoctorNoteFile.count() == initial_count + 1
    _, _, user = get_clerk_dev_user()
    doctor_note = DoctorNoteFile.get(azure_file_name=f"{user.id}_1234567890_valid.pdf")
    assert doctor_note is not None
    assert doctor_note.note_type == "SOAP Note"
    queue.assert_called_once_with(doctor_note.id)


def test_rejects_pdf_larger_than_ten_megabytes(
    authenticated_client: TestClient, monkeypatch
):
    _install_blob(
        monkeypatch,
        content_type="application/pdf",
        content=b"%PDF-",
        size=example_note_upload.MAX_EXAMPLE_NOTE_BYTES + 1,
    )
    queue = Mock()
    monkeypatch.setattr(app.jobs.extract, "queue", queue)
    initial_count = DoctorNoteFile.count()

    response = _register_example_note(authenticated_client, "too-large.pdf")

    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert response.json()["detail"] == "Example note PDFs must be 10 MB or smaller."
    assert DoctorNoteFile.count() == initial_count
    queue.assert_not_called()


def test_rejects_a_blob_owned_by_another_account_before_fetching(
    authenticated_client: TestClient, monkeypatch
):
    service_client = Mock()
    monkeypatch.setattr(
        example_note_upload,
        "BlobServiceClient",
        lambda *_args, **_kwargs: service_client,
    )
    queue = Mock()
    monkeypatch.setattr(app.jobs.extract, "queue", queue)
    initial_count = DoctorNoteFile.count()

    response = authenticated_client.post(
        api_app.url_path_for("upload_example_note"),
        params={
            "file_name": "user_someone_else_1234567890_note.pdf",
            "note_type": "SOAP Note",
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == (
        "The uploaded example note does not belong to this account."
    )
    assert DoctorNoteFile.count() == initial_count
    service_client.get_blob_client.assert_not_called()
    queue.assert_not_called()
