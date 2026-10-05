"""Durability, source invalidation, recovery, and canonical-action authorization."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.configuration.session_prep as configuration
import app.services.authority as authority
from app.configuration.session_prep import SessionPrepProviderSettings
from app.routes.agent import agent_api_app, get_agent_authority_settings
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_sovereign_tool_request,
)
from app.services.authority_conversation import write_conversation_turn
from app.services.session_prep import (
    SessionPrepPreparedBrief,
    SessionPrepScopeDenied,
    authorize_session_prep_read,
)
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    note_content_sha256,
)
from app.services.session_prep_runtime import (
    invoke_provider_session_prep,
)
from app.services.session_prep_worker import run_one_build

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.session_prep_runtime import (
    SessionPrepArtifact,
    SessionPrepBuild,
    SessionPrepReadReceipt,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory
from tests.routes.utils import provision_practice_in_force
from tests.services.session_prep_authority_test import AUTHORITY_SETTINGS, ISSUER
from tests.services.session_prep_note_sources_test import _note

WORKLOAD = "11111111-1111-4111-8111-111111111111"
NOW = datetime.now(UTC)


class _AuthorityClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


def _brief(selection):
    reference = selection.sources[0].source_ref
    item = {
        "text": "Review the documented synthetic goals.",
        "source_refs": [reference],
    }
    return SessionPrepPreparedBrief.model_validate(
        {
            "recap": {
                "text": "The prior reviewed note documents synthetic goals.",
                "source_refs": [reference],
            },
            "trajectory_and_material_changes": [],
            "talking_points": [item],
            "open_threads": [],
            "cautions": [],
            "suggested_outline": [item],
            "patient_reported": None,
        }
    )


@pytest.fixture
def scope(monkeypatch):
    # The exchange schedules using the authority clock. Keep it aligned with
    # the injected worker clock instead of depending on the wall time of day.
    monkeypatch.setattr(authority, "datetime", _AuthorityClock)
    doctor = DoctorFactory.build().save()
    provision_practice_in_force(doctor)
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    target = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id, appointment_date=NOW
    ).save()
    note = _note(doctor, patient)
    with get_session() as session:
        finalize_note_for_session_prep(
            session=session,
            doctor_id=doctor.id,
            note_id=note.id,
            expected_content_sha256=note_content_sha256(note.note),
            observed_at=NOW - timedelta(hours=8),
        )
        session.commit()
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True, frozenset({doctor.clerk_id})),
    )
    token = authority.exchange_context_token(
        clerk_issuer=ISSUER,
        clerk_subject=doctor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=str(target.id),
        settings=AUTHORITY_SETTINGS,
    ).context_token
    record = ProviderRecordAuthority.get(appointment_id=target.id)
    return doctor, target, note, token, record


def _action(token):
    client_turn_id = uuid4()
    resolution = authority.resolve_mount_context(
        workload_client_id=WORKLOAD,
        mount_context_token=token,
        client_turn_id=client_turn_id,
        settings=AUTHORITY_SETTINGS,
    )
    accepted = write_conversation_turn(
        workload_client_id=WORKLOAD,
        mount_context_token=token,
        interaction_ref=resolution.interaction_ref,
        client_turn_id=client_turn_id,
        idempotency_key=f"action-{client_turn_id}",
        settings=AUTHORITY_SETTINGS,
        payload={
            "phase": "user_accepted",
            "ingress_kind": "provider_ui_action",
            "action_code": "session-prep.open",
            "message": {"content_type": "text/plain", "content": "Open Session Prep"},
        },
    )
    return client_turn_id, resolution.interaction_ref, accepted.turn_ref


def _read(
    token, record, action, *, key="prep-read-key-0000001", context=None, detail="brief"
):
    client_turn_id, interaction_ref, turn_ref = action
    with get_session() as session:
        authorized = authorize_session_prep_read(
            session=session,
            workload_client_id=WORKLOAD,
            mount_context_token=token,
            interaction_ref=interaction_ref,
            client_turn_id=client_turn_id,
            source_turn_ref=turn_ref,
            settings=AUTHORITY_SETTINGS,
        )
        result = invoke_provider_session_prep(
            session=session,
            authorization=authorized,
            workload_client_id=WORKLOAD,
            idempotency_key=key,
            source_turn_ref=turn_ref,
            context_snapshot_ref=context or record.context_snapshot_ref,
            detail=detail,
            observed_at=NOW,
            settings=AUTHORITY_SETTINGS,
        )
        session.commit()
        return result


def _work(generate=_brief, *, now=NOW):
    return run_one_build(
        generate=generate, clock=lambda: now, settings=AUTHORITY_SETTINGS
    )


def test_pending_read_does_not_generate_and_ready_receipt_survives_new_sessions(scope):
    _doctor, _target, _note_row, token, record = scope
    action = _action(token)
    pending = _read(token, record, action)
    assert pending.response.result.state == "pending"
    assert SessionPrepArtifact.count() == 0
    assert _work() == "ready"
    ready = _read(token, record, action, key="prep-read-key-0000002")
    assert ready.response.result.state == "ready"
    replay = _read(token, record, action, key="prep-read-key-0000002")
    assert replay.replayed
    assert replay.response == ready.response
    assert SessionPrepArtifact.count() == 1
    assert SessionPrepReadReceipt.count() == 2
    artifact = SessionPrepArtifact.first()
    assert artifact is not None
    assert ready.response.result.brief_ref == f"brief_{artifact.id}"
    assert ready.response.result.snapshot_ref == f"snapshot_{artifact.id}"
    assert ready.response.tool_call_ref.startswith("prep_read_")
    assert (
        AuthorityAuditEvent.where(
            AuthorityAuditEvent.event_type == "session_prep.context.read"
        ).count()
        == 2
    )
    assert "synthetic goals" not in repr(SessionPrepArtifact.first())


def test_correction_immediately_suppresses_previously_ready_content_and_replay(scope):
    doctor, _target, note, token, record = scope
    assert _work() == "ready"
    action = _action(token)
    assert _read(token, record, action).response.result.state == "ready"
    note.note = {"Plan": "Review the corrected synthetic concern."}
    note.save()
    with pytest.raises(SessionPrepScopeDenied):
        _read(token, record, action)
    assert _read(token, record, _action(token)).response.result.state == "unavailable"
    with get_session() as session:
        finalize_note_for_session_prep(
            session=session,
            doctor_id=doctor.id,
            note_id=note.id,
            expected_content_sha256=note_content_sha256(note.note),
            observed_at=NOW,
        )
        session.commit()
    next_action = _action(token)
    assert _read(token, record, next_action).response.result.state == "pending"
    assert _work() == "ready"
    refreshed = _read(
        token, record, next_action, key="prep-read-key-0000002"
    ).response.result
    assert refreshed.brief_version == 2
    assert refreshed.brief_supersedes_ref is not None
    assert SessionPrepArtifact.count() == 2


def test_record_handle_and_supporting_context_cannot_be_forged(scope):
    _doctor, _target, _note_row, token, record = scope
    action = _action(token)
    with pytest.raises(SessionPrepScopeDenied):
        _read(token, record, action, context="context_prep_forged")
    with pytest.raises(SessionPrepScopeDenied):
        _read(token, record, action, detail="supporting_context")
    assert SessionPrepReadReceipt.count() == 0


def test_worker_rechecks_sources_after_the_model_call(scope):
    _doctor, _target, note, token, record = scope

    def changed_during_generation(selection):
        note.note = {"Plan": "A corrected synthetic note requires review."}
        note.save()
        return _brief(selection)

    assert _work(changed_during_generation) == "superseded"
    assert SessionPrepArtifact.count() == 0
    assert _read(token, record, _action(token)).response.result.state == "unavailable"


@pytest.mark.parametrize(
    "unsafe", ["Start taking 20 mg of medication.", "This is a new diagnosis."]
)
def test_unsafe_generation_is_never_persisted(scope, unsafe):
    def bad_brief(selection):
        result = _brief(selection)
        result.recap.text = unsafe
        return result

    assert _work(bad_brief) == "failed"
    assert SessionPrepArtifact.count() == 0
    assert SessionPrepBuild.first().failure_code == "invalid_brief"


def test_expired_worker_lease_is_reclaimed_and_failure_backoff_is_bounded(scope):
    build = SessionPrepBuild.first()
    assert build is not None
    build.state, build.attempts, build.lease_until = (
        "running",
        1,
        NOW - timedelta(seconds=1),
    )
    build.save()
    assert _work() == "ready"
    assert SessionPrepArtifact.count() == 1


def test_model_failures_do_not_leave_running_jobs_or_store_exception_text(scope):
    def fails(_selection):
        raise RuntimeError("synthetic-private-error-sentinel")

    assert _work(fails) == "failed"
    assert SessionPrepBuild.first().state == "pending"
    assert _work(fails, now=NOW + timedelta(seconds=1)) == "idle"
    assert _work(fails, now=NOW + timedelta(seconds=16)) == "failed"
    assert _work(fails, now=NOW + timedelta(seconds=47)) == "failed"
    failed = SessionPrepBuild.first()
    assert failed is not None
    assert failed.state == "failed" and failed.attempts == 3
    assert failed.lease_until is None
    assert "private-error" not in failed.model_dump_json()
    assert SessionPrepArtifact.count() == 0


def test_ready_artifacts_and_receipts_cannot_be_rewritten(scope):
    _doctor, _target, _note_row, token, record = scope
    assert _work() == "ready"
    _read(token, record, _action(token))
    for statement in (
        "UPDATE session_prep_artifact SET result_payload = '{}'::jsonb",
        "UPDATE session_prep_read_receipt SET response_payload = '{}'::jsonb",
    ):
        with get_session() as session:
            with pytest.raises(DBAPIError):
                session.exec(text(statement))
                session.flush()
            session.rollback()


def _client():
    app = FastAPI()
    app.include_router(agent_api_app)
    app.dependency_overrides[authenticate_sovereign_tool_request] = (
        lambda: EntraServicePrincipal(client_id=WORKLOAD)
    )
    app.dependency_overrides[get_agent_authority_settings] = lambda: AUTHORITY_SETTINGS
    return TestClient(app)


def _post(client, token, record, action):
    client_turn_id, interaction_ref, turn_ref = action
    return client.post(
        "/service/v1/agent/tools/invoke",
        headers={
            "X-Cosmaneura-Mount-Context": token,
            "X-Cosmaneura-Interaction-Ref": interaction_ref,
            "X-Cosmaneura-Client-Turn-ID": str(client_turn_id),
            "Idempotency-Key": "api-prep-read-0000001",
        },
        json={
            "tool_name": "session-prep.context.read",
            "tool_version": "1.0.0",
            "subject_ref": None,
            "arguments": {
                "source_turn_ref": turn_ref,
                "context_snapshot_ref": record.context_snapshot_ref,
                "purpose": "session_prep",
                "detail": "brief",
            },
        },
    )


def test_real_receiver_commits_before_egress_and_denies_replay_after_revocation(
    scope, monkeypatch
):
    _doctor, _target, _note_row, token, record = scope
    assert _work() == "ready"
    client, action = _client(), _action(token)
    response = _post(client, token, record, action)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["result"]["state"] == "ready"
    assert SessionPrepReadReceipt.count() == 1
    assert (
        _post(client, token, record, action).headers["Idempotency-Replayed"] == "true"
    )
    monkeypatch.setattr(
        configuration, "SESSION_PREP_PROVIDER_SETTINGS", SessionPrepProviderSettings()
    )
    revoked = _post(client, token, record, action)
    assert revoked.status_code in {403, 503}
    assert "synthetic goals" not in revoked.text


def test_worker_cannot_publish_after_its_lease_expires(scope):
    ticks = iter([NOW, NOW + timedelta(seconds=91)])
    assert (
        run_one_build(
            generate=_brief, clock=lambda: next(ticks), settings=AUTHORITY_SETTINGS
        )
        == "discarded"
    )
    assert SessionPrepArtifact.count() == 0
    assert _work(now=NOW + timedelta(seconds=92)) == "ready"


def test_generator_cannot_attach_another_records_citation(scope):
    def forged(selection):
        result = _brief(selection)
        result.recap.source_refs = ["note_foreign_synthetic_0001"]
        return result

    assert _work(forged) == "failed"
    assert SessionPrepArtifact.count() == 0
