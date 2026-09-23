"""Only the current clinician can review exactly the displayed note revision."""

from datetime import datetime

import pytest

import app.configuration.session_prep as configuration
from app.configuration.session_prep import SessionPrepProviderSettings
from app.server import api_app
from app.services.session_prep_note_sources import note_content_sha256

from app.models.doctor import Doctor
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization

from tests.factories import PatientFactory
from tests.routes.utils import get_clerk_dev_user
from tests.services.session_prep_note_sources_test import _note, _scope


@pytest.fixture
def reviewable_note(authenticated_client, monkeypatch):
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    note = _note(doctor, patient)
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True, frozenset({doctor.clerk_id})),
    )
    return note


def _review(client, note, **body):
    return client.post(
        api_app.url_path_for("review_note_for_session_prep", note_id=note.id),
        json={"expected_content_sha256": note_content_sha256(note.note), **body},
    )


def test_review_binds_displayed_revision_and_survives_a_new_read(
    authenticated_client, reviewable_note
):
    note = reviewable_note
    path = api_app.url_path_for("note_detail", appointment_id=note.appointment_id)
    detail = authenticated_client.get(path).json()
    assert len(detail["notes"]) == 1
    assert detail["notes"][0]["note"] == note.note
    digest = detail["session_prep_reviews"][str(note.id)]["content_sha256"]
    assert detail["session_prep_reviews"][str(note.id)]["reviewed_at"] is None
    reviewed = _review(authenticated_client, note, expected_content_sha256=digest)
    assert reviewed.status_code == 200
    assert "no-store" in reviewed.headers["cache-control"]
    assert reviewed.json()["reviewed_at"] is not None
    assert set(reviewed.json()) == {"content_sha256", "reviewed_at"}
    assert _review(authenticated_client, note).json() == reviewed.json()
    assert SessionPrepNoteFinalization.count() == 1
    reloaded = authenticated_client.get(path).json()
    assert reloaded["session_prep_reviews"][str(note.id)] == reviewed.json()


@pytest.mark.parametrize("disabled", ["switch", "cohort"])
def test_review_is_default_off_and_scoped_to_selected_providers(
    authenticated_client, reviewable_note, monkeypatch, disabled
):
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(
            enabled=disabled != "switch",
            clerk_subjects=(
                frozenset({"user_unrelated"})
                if disabled == "cohort"
                else configuration.SESSION_PREP_PROVIDER_SETTINGS.clerk_subjects
            ),
        ),
    )
    assert _review(authenticated_client, reviewable_note).status_code == 404
    path = api_app.url_path_for(
        "note_detail", appointment_id=reviewable_note.appointment_id
    )
    assert authenticated_client.get(path).json()["session_prep_reviews"] is None
    assert SessionPrepNoteFinalization.count() == 0


def test_other_provider_note_cannot_be_reviewed(authenticated_client, reviewable_note):
    outsider, patient, _ = _scope()
    foreign_note = _note(outsider, patient)
    assert _review(authenticated_client, foreign_note).status_code == 404
    assert SessionPrepNoteFinalization.count() == 0


def test_stale_and_superseded_revisions_are_not_reviewed(
    authenticated_client, reviewable_note
):
    note = reviewable_note
    assert (
        _review(
            authenticated_client, note, expected_content_sha256="0" * 64
        ).status_code
        == 409
    )
    note.superseded_at = datetime.now()
    note.supersession_reason = "explicit_regeneration"
    note.save()
    assert _review(authenticated_client, note).status_code == 404
    assert SessionPrepNoteFinalization.count() == 0


def test_review_rejects_body_identity_and_unbound_browser_request(
    authenticated_client, reviewable_note
):
    note = reviewable_note
    assert (
        _review(authenticated_client, note, doctor_id=str(note.doctor_id)).status_code
        == 422
    )
    authenticated_client.headers.pop("X-Cosmaneura-View-As-Binding")
    assert _review(authenticated_client, note).status_code == 409
    assert SessionPrepNoteFinalization.count() == 0
