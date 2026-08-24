from unittest.mock import Mock

from fastapi import status
from fastapi.testclient import TestClient

import app.jobs.extract
import app.services.example_note_upload as example_note_upload
from app.server import api_app

from app.models.doctor_note_file import DoctorNoteFile


def test_issues_server_owned_example_note_upload_grant(
    authenticated_client: TestClient,
    monkeypatch,
):
    issue = Mock(
        return_value=example_note_upload.IssuedExampleNoteUpload(
            upload_url="https://uploads.example/pending/random.pdf?sp=c&sig=secret",
            registration_token="opaque-registration-token",
        )
    )
    monkeypatch.setattr(example_note_upload, "issue_example_note_upload", issue)

    response = authenticated_client.post(
        api_app.url_path_for("generate_signed_example_note_upload_url")
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "upload_url": "https://uploads.example/pending/random.pdf?sp=c&sig=secret",
        "registration_token": "opaque-registration-token",
    }
    (doctor_id,) = issue.call_args.args
    assert doctor_id.startswith("doctor_")


def test_registers_only_the_server_resolved_blob_and_queues_once(
    authenticated_client: TestClient,
    monkeypatch,
):
    registered = example_note_upload.RegisteredExampleNoteBlob(
        blob_key="pending/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.pdf",
        doctor_id="doctor_bound",
        etag='"etag-1"',
        size=17,
        sha256="a" * 64,
    )
    consume = Mock(return_value=registered)
    serialize = Mock(return_value="example-note:v1:encrypted-reference")
    queue = Mock()
    monkeypatch.setattr(
        example_note_upload,
        "consume_validated_example_note_upload",
        consume,
    )
    monkeypatch.setattr(example_note_upload, "serialize_registered_blob", serialize)
    monkeypatch.setattr(app.jobs.extract, "queue", queue)
    initial_count = DoctorNoteFile.count()

    response = authenticated_client.post(
        api_app.url_path_for("upload_example_note"),
        json={
            "registration_token": "A" * 43,
            "note_type": "SOAP Note",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert DoctorNoteFile.count() == initial_count + 1
    doctor_note = DoctorNoteFile.get(
        azure_file_name="example-note:v1:encrypted-reference"
    )
    assert doctor_note is not None
    assert doctor_note.note_type == "SOAP Note"
    consume.assert_called_once()
    assert consume.call_args.args[0] == "A" * 43
    assert consume.call_args.args[1] == str(doctor_note.doctor_id)
    serialize.assert_called_once_with(registered)
    queue.assert_called_once_with(doctor_note.id)


def test_registration_rejects_client_selected_blob_name(
    authenticated_client: TestClient,
    monkeypatch,
):
    consume = Mock()
    queue = Mock()
    monkeypatch.setattr(
        example_note_upload,
        "consume_validated_example_note_upload",
        consume,
    )
    monkeypatch.setattr(app.jobs.extract, "queue", queue)

    response = authenticated_client.post(
        api_app.url_path_for("upload_example_note"),
        json={
            "registration_token": "A" * 43,
            "note_type": "SOAP Note",
            "file_name": "user_chosen.pdf",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    consume.assert_not_called()
    queue.assert_not_called()


def test_legacy_query_registration_contract_is_closed(
    authenticated_client: TestClient,
    monkeypatch,
):
    consume = Mock()
    queue = Mock()
    monkeypatch.setattr(
        example_note_upload,
        "consume_validated_example_note_upload",
        consume,
    )
    monkeypatch.setattr(app.jobs.extract, "queue", queue)

    response = authenticated_client.post(
        api_app.url_path_for("upload_example_note"),
        params={"file_name": "attacker.pdf", "note_type": "SOAP Note"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    consume.assert_not_called()
    queue.assert_not_called()


def test_validation_failure_keeps_pdf_only_error_and_does_not_persist_or_queue(
    authenticated_client: TestClient,
    monkeypatch,
):
    consume = Mock(
        side_effect=example_note_upload.ExampleNoteUploadValidationError(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Example notes must be valid PDF files.",
        )
    )
    queue = Mock()
    monkeypatch.setattr(
        example_note_upload,
        "consume_validated_example_note_upload",
        consume,
    )
    monkeypatch.setattr(app.jobs.extract, "queue", queue)
    initial_count = DoctorNoteFile.count()

    response = authenticated_client.post(
        api_app.url_path_for("upload_example_note"),
        json={
            "registration_token": "A" * 43,
            "note_type": "SOAP Note",
        },
    )

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert response.json()["detail"] == "Example notes must be valid PDF files."
    assert DoctorNoteFile.count() == initial_count
    queue.assert_not_called()
