"""Only the current clinician can review exactly the displayed note revision."""

from datetime import datetime

import pytest
from fastapi import Request

import app.configuration.session_prep as configuration
from app.configuration.session_prep import SessionPrepProviderSettings
from app.routes.dependencies.view_as import apply_view_as
from app.server import api_app
from app.services.session_prep_note_sources import note_content_sha256

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization

from tests.factories import AppointmentFactory, PatientFactory
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


def test_current_visit_exposes_only_prior_note_metadata_and_refreshes_after_review(
    authenticated_client, reviewable_note
):
    note = reviewable_note
    source = AIVisitTranscript.one(id=note.ai_transcript_id)
    target = AppointmentFactory.build(
        doctor_id=note.doctor_id,
        patient_id=source.patient_id,
        appointment_date=datetime.now(),
    ).save()
    path = api_app.url_path_for("note_detail", appointment_id=target.id)
    before = authenticated_client.get(path)
    assert before.status_code == 200 and "no-store" in before.headers["cache-control"]
    preview = before.json()["session_prep_prior_notes"]
    assert preview["included_count"] == 0
    assert preview["notes"][0]["note_id"] == str(note.id)
    assert preview["notes"][0]["status"] == "needs_review"
    source_detail = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=note.appointment_id)
    ).json()
    assert (
        preview["notes"][0]["appointment_date"]
        == source_detail["appointment"]["appointment_date"]
    )
    assert set(preview["notes"][0]) == {
        "note_id",
        "appointment_id",
        "appointment_date",
        "note_type",
        "status",
    }
    assert _review(authenticated_client, note).status_code == 200
    after = authenticated_client.get(path).json()["session_prep_prior_notes"]
    assert after["included_count"] == 1 and after["notes"][0]["status"] == "included"


def test_scribe_note_keeps_its_source_and_review_when_another_transcript_arrives(
    authenticated_client, reviewable_note
):
    note = reviewable_note
    source = AIVisitTranscript.one(id=note.ai_transcript_id)
    AIVisitTranscript(
        doctor_id=source.doctor_id,
        patient_id=source.patient_id,
        appointment_id=source.appointment_id,
        duration=1,
        transcript_data=[],
    ).save()
    path = api_app.url_path_for("note_detail", appointment_id=note.appointment_id)
    response = authenticated_client.get(path)
    assert response.status_code == 200
    detail = response.json()
    assert detail["transcript"]["id"] == str(source.id)
    assert [row["id"] for row in detail["note_transcripts"]] == [str(source.id)]
    assert detail["session_prep_reviews"][str(note.id)]["content_sha256"] == (
        note_content_sha256(note.note)
    )
    assert _review(authenticated_client, note).status_code == 200


def test_two_current_notes_keep_distinct_sources_and_reviews(
    authenticated_client, reviewable_note
):
    first = reviewable_note
    original = AIVisitTranscript.one(id=first.ai_transcript_id)
    second_source = AIVisitTranscript(
        doctor_id=original.doctor_id,
        patient_id=original.patient_id,
        appointment_id=original.appointment_id,
        duration=1,
        transcript_data=[],
    ).save()
    schema = DoctorNoteSchema(
        doctor_id=first.doctor_id,
        note_type="Synthetic second template",
        fields={"Plan": "Documented follow-up"},
    ).save()
    second = AIVisitNote(
        doctor_id=first.doctor_id,
        appointment_id=first.appointment_id,
        ai_transcript_id=second_source.id,
        doctor_note_schema_id=schema.id,
        note={"Plan": "Distinct synthetic source."},
    ).save()
    assert _review(authenticated_client, first).status_code == 200
    detail = authenticated_client.get(
        api_app.url_path_for("note_detail", appointment_id=first.appointment_id)
    ).json()
    assert detail["transcript"] is None
    assert {row["id"] for row in detail["note_transcripts"]} == {
        str(original.id),
        str(second_source.id),
    }
    assert {row["id"]: row["ai_transcript_id"] for row in detail["notes"]} == {
        str(first.id): str(original.id),
        str(second.id): str(second_source.id),
    }
    assert detail["session_prep_reviews"][str(first.id)]["reviewed_at"] is not None
    assert detail["session_prep_reviews"][str(second.id)]["reviewed_at"] is None


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
    assert authenticated_client.get(path).json()["session_prep_prior_notes"] is None
    assert SessionPrepNoteFinalization.count() == 0


def test_other_provider_note_cannot_be_reviewed(authenticated_client, reviewable_note):
    outsider, patient, _ = _scope()
    foreign_note = _note(outsider, patient)
    assert _review(authenticated_client, foreign_note).status_code == 404
    assert SessionPrepNoteFinalization.count() == 0


def test_view_as_read_does_not_expose_prior_note_review_workflow(
    authenticated_client, reviewable_note
):
    def force_view_as(request: Request) -> None:
        request.state.view_as_actor = request.state.doctor
        request.state.view_as = object()

    api_app.dependency_overrides[apply_view_as] = force_view_as
    try:
        response = authenticated_client.get(
            api_app.url_path_for(
                "note_detail", appointment_id=reviewable_note.appointment_id
            )
        )
    finally:
        del api_app.dependency_overrides[apply_view_as]
    assert response.status_code == 200
    assert response.json()["session_prep_prior_notes"] is None
    assert response.json()["session_prep_reviews"] is None


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
