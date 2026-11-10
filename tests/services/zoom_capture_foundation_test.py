import hashlib
import importlib
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from activemodel.session_manager import get_engine, get_session
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.patient import Patient
from app.models.zoom_capture import (
    ZoomCaptureSegment,
    ZoomCaptureSession,
    ZoomClinicalRoom,
    ZoomOccurrence,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _digest(label: str) -> str:
    nonce = secrets.token_hex(8)
    return hashlib.sha256(f"synthetic:{label}:{nonce}".encode()).hexdigest()


def _host_digest(provider_user_id: str) -> str:
    return hashlib.sha256(f"zoom-host:{provider_user_id}".encode()).hexdigest()


def _doctor(label: str) -> Doctor:
    nonce = secrets.token_hex(6)
    return Doctor(
        clerk_id=f"user_zoom_foundation_{label}_{nonce}",
        email=f"zoom-foundation-{label}-{nonce}@example.test",
        biography="Synthetic test provider",
    ).save()


def _integration(
    doctor: Doctor,
    label: str,
    *,
    provider_user_id: str | None = None,
) -> DoctorVideoIntegration:
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=(
            provider_user_id or f"synthetic_zoom_host_{label}_{secrets.token_hex(6)}"
        ),
        access_token="synthetic-access-token",
        refresh_token="synthetic-refresh-token",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    ).save()


def _patient(doctor: Doctor, label: str) -> Patient:
    return Patient(
        doctor_id=doctor.id,
        name=f"Synthetic Patient {label}",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()


def _appointment(
    doctor: Doctor,
    patient: Patient,
    label: str,
) -> Appointment:
    return Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(UTC),
        external_id=f"synthetic-appointment-{label}-{secrets.token_hex(4)}",
    ).save()


def _occurrence(
    doctor: Doctor,
    integration: DoctorVideoIntegration,
    save: bool = True,
    **overrides,
) -> ZoomOccurrence:
    meeting_digest = overrides.pop("zoom_meeting_id_digest", _digest("meeting"))
    clinical_room_id = overrides.pop("clinical_room_id", None)
    if clinical_room_id is None:
        clinical_room_id = _room(doctor, meeting_digest).id
    values = {
        "doctor_id": doctor.id,
        "video_integration_id": integration.id,
        "clinical_room_id": clinical_room_id,
        "zoom_meeting_id_digest": meeting_digest,
        "zoom_host_id_digest": _host_digest(integration.provider_user_id),
        "occurrence_digest": _digest("occurrence"),
        "started_at": datetime.now(UTC),
    }
    values.update(overrides)
    occurrence = ZoomOccurrence(**values)
    return occurrence.save() if save else occurrence


def _room(doctor: Doctor, meeting_digest: str) -> ZoomClinicalRoom:
    return ZoomClinicalRoom(
        doctor_id=doctor.id,
        zoom_meeting_id_ciphertext="synthetic-meeting-id",
        zoom_meeting_id_digest=meeting_digest,
        designated_at=datetime.now(UTC),
    ).save()


def _capture_session(
    doctor: Doctor,
    occurrence: ZoomOccurrence,
    sequence: int,
    *,
    appointment: Appointment | None = None,
    patient: Patient | None = None,
    lifecycle_state: str = "open",
) -> ZoomCaptureSession:
    now = datetime.now(UTC)
    assigned = appointment is not None or patient is not None
    assert assigned == (appointment is not None and patient is not None)
    return ZoomCaptureSession(
        doctor_id=doctor.id,
        occurrence_id=occurrence.id,
        sequence=sequence,
        lifecycle_state=lifecycle_state,
        assignment_state="assigned" if assigned else "unassigned",
        started_at=now,
        ended_at=now if lifecycle_state != "open" else None,
        appointment_id=appointment.id if appointment is not None else None,
        patient_id=patient.id if patient is not None else None,
        assignment_basis="provider_confirmed" if assigned else None,
        assigned_at=now if assigned else None,
    ).save()


def _segment(
    doctor: Doctor,
    capture_session: ZoomCaptureSession,
    sequence: int,
    transport_state: str,
    save: bool = True,
    **overrides,
) -> ZoomCaptureSegment:
    now = datetime.now(UTC)
    values = {
        "doctor_id": doctor.id,
        "capture_session_id": capture_session.id,
        "sequence": sequence,
        "transport_state": transport_state,
        "started_at": now,
        "stopped_at": (
            None
            if transport_state in {"starting", "recording", "stopping", "uncertain"}
            else now
        ),
    }
    values.update(overrides)
    segment = ZoomCaptureSegment(**values)
    return segment.save() if save else segment


def _assert_model_rejected(record, message: str) -> None:
    with get_session() as session:
        savepoint = session.begin_nested()
        session.add(record)
        with pytest.raises(DBAPIError, match=message):
            session.flush()
        savepoint.rollback()


def _assert_deferred_update_rejected(
    statement: str,
    params: dict[str, object],
    message: str,
) -> None:
    with get_session() as session:
        savepoint = session.begin_nested()
        session.execute(text(statement), params=params)
        with pytest.raises(DBAPIError, match=message):
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        savepoint.rollback()


def _assert_update_rejected(
    statement: str,
    params: dict[str, object],
    message: str,
) -> None:
    with get_session() as session:
        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match=message):
            session.execute(text(statement), params=params)
        savepoint.rollback()


def _insert_room(connection, doctor_id: object, suffix: str) -> object:
    room_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO zoom_clinical_room "
            "(id, doctor_id, zoom_meeting_id_ciphertext, zoom_meeting_id_digest, "
            "state, designated_at) VALUES "
            "(:id, :doctor, :ciphertext, :digest, 'active', now())"
        ),
        {
            "id": room_id,
            "doctor": doctor_id,
            "ciphertext": f"synthetic-{suffix}",
            "digest": _digest(suffix),
        },
    )
    return room_id


def test_occurrence_rejects_another_doctors_video_integration() -> None:
    integration_owner = _doctor("integration-owner")
    other_doctor = _doctor("integration-other")
    integration = _integration(integration_owner, "owned")
    meeting_digest = _digest("cross-doctor-meeting")
    room = _room(other_doctor, meeting_digest)

    _assert_model_rejected(
        ZoomOccurrence(
            doctor_id=other_doctor.id,
            video_integration_id=integration.id,
            clinical_room_id=room.id,
            zoom_meeting_id_digest=meeting_digest,
            zoom_host_id_digest=_host_digest(integration.provider_user_id),
            occurrence_digest=_digest("cross-doctor-occurrence"),
            started_at=datetime.now(UTC),
        ),
        "zoom occurrence integration scope mismatch",
    )


def test_occurrence_rejects_a_stale_zoom_host_identity() -> None:
    doctor = _doctor("stale-host")
    integration = _integration(doctor, "stale-host")
    _assert_model_rejected(
        _occurrence(
            doctor,
            integration,
            save=False,
            zoom_host_id_digest=_host_digest("replaced-host"),
        ),
        "zoom occurrence integration scope mismatch",
    )


def test_designated_room_must_be_active_and_match_the_occurrence() -> None:
    doctor = _doctor("room-scope")
    integration = _integration(doctor, "room-scope")
    meeting_digest = _digest("designated-room")
    room = _room(doctor, meeting_digest)

    _assert_model_rejected(
        ZoomOccurrence(
            doctor_id=doctor.id,
            video_integration_id=integration.id,
            clinical_room_id=room.id,
            zoom_meeting_id_digest=_digest("other-room"),
            zoom_host_id_digest=_host_digest(integration.provider_user_id),
            occurrence_digest=_digest("wrong-room-occurrence"),
            started_at=datetime.now(UTC),
        ),
        "zoom occurrence clinical room is not active or in scope",
    )
    other = _doctor("room-scope-other")
    _assert_model_rejected(
        ZoomClinicalRoom(
            doctor_id=other.id,
            zoom_meeting_id_ciphertext="same-synthetic-meeting-id",
            zoom_meeting_id_digest=meeting_digest,
            designated_at=datetime.now(UTC),
        ),
        "uq_zoom_clinical_room_active_meeting",
    )
    occurrence = _occurrence(
        doctor,
        integration,
        clinical_room_id=room.id,
        zoom_meeting_id_digest=meeting_digest,
    )
    room.state = "disabled"
    room.disabled_at = datetime.now(UTC)
    room.save()
    ended = ZoomOccurrence.get(id=occurrence.id)
    assert ended is not None
    assert (ended.lifecycle_state, ended.bot_state, ended.version) == (
        "ended",
        "uncertain",
        2,
    )
    assert ended.ended_at is not None and ended.ended_at >= ended.started_at
    _assert_model_rejected(
        ZoomOccurrence(
            doctor_id=doctor.id,
            video_integration_id=integration.id,
            clinical_room_id=room.id,
            zoom_meeting_id_digest=meeting_digest,
            zoom_host_id_digest=_host_digest(integration.provider_user_id),
            occurrence_digest=_digest("disabled-room-occurrence"),
            started_at=datetime.now(UTC),
        ),
        "zoom occurrence clinical room is not active or in scope",
    )
    assert _room(other, meeting_digest).state == "active"


def test_same_zoom_host_can_route_through_distinct_provider_rooms() -> None:
    host_id = f"synthetic_shared_host_{secrets.token_hex(6)}"
    for label in ("first", "second"):
        doctor = _doctor(f"shared-host-{label}")
        integration = _integration(doctor, label, provider_user_id=host_id)
        meeting_digest = _digest(f"room-{label}")
        room = _room(doctor, meeting_digest)
        occurrence = _occurrence(
            doctor,
            integration,
            clinical_room_id=room.id,
            zoom_meeting_id_digest=meeting_digest,
        )
        assert occurrence.doctor_id == doctor.id


def test_room_disable_serializes_the_active_room_lookup() -> None:
    engine = get_engine()
    with engine.begin() as setup:
        doctor_id = setup.execute(text("SELECT id FROM doctor LIMIT 1")).scalar_one()
        room_id = _insert_room(setup, doctor_id, "disable-race")

    def find_active_room():
        with engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT 1 FROM zoom_clinical_room "
                    "WHERE id = :id AND state = 'active' FOR SHARE"
                ),
                {"id": room_id},
            ).scalar_one_or_none()

    with engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(
            text(
                "UPDATE zoom_clinical_room SET state = 'disabled', "
                "disabled_at = now() WHERE id = :id"
            ),
            {"id": room_id},
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(find_active_room)
            time.sleep(0.2)
            assert not waiting.done()
            transaction.commit()
            assert waiting.result(timeout=5) is None
    with engine.begin() as cleanup:
        cleanup.execute(
            text("DELETE FROM zoom_clinical_room WHERE id = :id"), {"id": room_id}
        )


def test_occurrence_and_recall_bot_identities_are_global() -> None:
    first = _doctor("global-first")
    second = _doctor("global-second")
    occurrence_digest = _digest("global-occurrence")
    bot_digest = _digest("global-bot")
    _occurrence(
        first,
        _integration(first, "global-first"),
        occurrence_digest=occurrence_digest,
        recall_bot_id_digest=bot_digest,
    )
    integration = _integration(second, "global-second")
    meeting_digest = _digest("global-second-room")
    room = _room(second, meeting_digest)
    values: dict[str, Any] = dict(
        doctor_id=second.id,
        video_integration_id=integration.id,
        clinical_room_id=room.id,
        zoom_meeting_id_digest=meeting_digest,
        zoom_host_id_digest=_host_digest(integration.provider_user_id),
        started_at=datetime.now(UTC),
    )
    _assert_model_rejected(
        ZoomOccurrence(**values, occurrence_digest=occurrence_digest),
        "uq_zoom_occurrence_digest",
    )
    _assert_model_rejected(
        ZoomOccurrence(
            **values,
            occurrence_digest=_digest("other-occurrence"),
            recall_bot_id_digest=bot_digest,
        ),
        "uq_zoom_occurrence_recall_bot_digest",
    )


@pytest.mark.parametrize("mutation", ["reconnect", "delete"])
def test_credential_loss_detaches_and_fences_an_active_occurrence(
    mutation: str,
) -> None:
    doctor = _doctor(f"credential-{mutation}")
    integration = _integration(doctor, f"credential-{mutation}")
    occurrence = _occurrence(doctor, integration)
    host_digest = occurrence.zoom_host_id_digest

    if mutation == "reconnect":
        integration.provider_user_id = f"replacement-{secrets.token_hex(6)}"
        integration.save()
    else:
        integration.delete()

    persisted = ZoomOccurrence.get(id=occurrence.id)
    assert persisted is not None
    assert persisted.video_integration_id is None
    assert persisted.zoom_host_id_digest == host_digest
    assert persisted.bot_state == "uncertain"
    assert persisted.version == 2


def test_assignment_rejects_cross_doctor_appointment_and_patient_scope() -> None:
    owner = _doctor("assignment-owner")
    other = _doctor("assignment-other")
    occurrence = _occurrence(owner, _integration(owner, "assignment"))
    owner_patient = _patient(owner, "Owner")
    owner_appointment = _appointment(owner, owner_patient, "owner")
    other_patient = _patient(other, "Other")
    other_appointment = _appointment(other, other_patient, "other")
    now = datetime.now(UTC)

    for sequence, appointment, patient in (
        (1, other_appointment, other_patient),
        (2, owner_appointment, other_patient),
    ):
        _assert_model_rejected(
            ZoomCaptureSession(
                doctor_id=owner.id,
                occurrence_id=occurrence.id,
                sequence=sequence,
                lifecycle_state="open",
                assignment_state="assigned",
                started_at=now,
                appointment_id=appointment.id,
                patient_id=patient.id,
                assignment_basis="provider_confirmed",
                assigned_at=now,
            ),
            "zoom capture appointment scope mismatch",
        )


def test_assigned_session_cannot_be_reassigned() -> None:
    doctor = _doctor("immutable-assignment")
    occurrence = _occurrence(doctor, _integration(doctor, "immutable-assignment"))
    first_patient = _patient(doctor, "First")
    first_appointment = _appointment(doctor, first_patient, "first")
    second_patient = _patient(doctor, "Second")
    second_appointment = _appointment(doctor, second_patient, "second")
    capture_session = _capture_session(
        doctor,
        occurrence,
        1,
        appointment=first_appointment,
        patient=first_patient,
    )

    _assert_update_rejected(
        "UPDATE zoom_capture_session SET appointment_id = :appointment, "
        "patient_id = :patient WHERE id = :capture",
        {
            "appointment": second_appointment.id.uuid,
            "patient": second_patient.id.uuid,
            "capture": capture_session.id.uuid,
        },
        "zoom capture assignment is immutable",
    )


def test_referenced_parent_ownership_changes_are_rejected() -> None:
    owner = _doctor("parent-scope-owner")
    other = _doctor("parent-scope-other")
    integration = _integration(owner, "parent-scope")
    occurrence = _occurrence(owner, integration)
    patient = _patient(owner, "Parent Scope")
    appointment = _appointment(owner, patient, "parent-scope")
    replacement_patient = _patient(owner, "Parent Scope Replacement")
    replacement_appointment = _appointment(
        owner,
        replacement_patient,
        "parent-scope-replacement",
    )
    capture_session = _capture_session(
        owner,
        occurrence,
        1,
        appointment=appointment,
        patient=patient,
    )
    transcript = AIVisitTranscript(
        doctor_id=owner.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=30,
        transcript_data=[],
    ).save()
    now = datetime.now(UTC)
    _segment(
        owner,
        capture_session,
        1,
        "complete",
        content_state="canonical",
        transcript_content_digest=_digest("parent-scope-content"),
        transcript_content_expires_at=now + timedelta(days=1),
        transcript_content_purged_at=now,
        appointment_id=appointment.id,
        patient_id=patient.id,
        result_transcript_id=transcript.id,
    )

    _assert_deferred_update_rejected(
        "UPDATE appointment SET patient_id = :patient_id WHERE id = :id",
        {"patient_id": replacement_patient.id.uuid, "id": appointment.id.uuid},
        "zoom capture appointment scope mismatch",
    )
    _assert_deferred_update_rejected(
        "UPDATE patient SET doctor_id = :doctor_id WHERE id = :id",
        {"doctor_id": other.id.uuid, "id": patient.id.uuid},
        "zoom capture patient scope mismatch",
    )
    _assert_deferred_update_rejected(
        "UPDATE ai_visit_transcript "
        "SET appointment_id = :appointment_id, patient_id = :patient_id "
        "WHERE id = :id",
        {
            "appointment_id": replacement_appointment.id.uuid,
            "patient_id": replacement_patient.id.uuid,
            "id": transcript.id.uuid,
        },
        "zoom capture transcript scope mismatch",
    )


def test_room_occurrence_session_and_pending_segment_lineage_is_immutable() -> None:
    owner = _doctor("lineage-owner")
    other = _doctor("lineage-other")
    occurrence = _occurrence(owner, _integration(owner, "lineage-owner"))
    other_occurrence = _occurrence(other, _integration(other, "lineage-other"))
    assert other_occurrence.video_integration_id is not None
    capture = _capture_session(owner, occurrence, 1)
    other_capture = _capture_session(other, other_occurrence, 1)
    now = datetime.now(UTC)
    pending = _segment(
        owner,
        capture,
        1,
        "complete",
        content_state="pending_assignment",
        transcript_content_ciphertext="synthetic pending content",
        transcript_content_digest=_digest("pending-lineage"),
        transcript_content_expires_at=now + timedelta(days=1),
    )

    _assert_update_rejected(
        "UPDATE zoom_clinical_room SET doctor_id = :doctor WHERE id = :id",
        {"doctor": other.id.uuid, "id": occurrence.clinical_room_id.uuid},
        "zoom clinical room lineage is immutable",
    )
    _assert_update_rejected(
        "UPDATE zoom_occurrence SET occurrence_digest = :digest WHERE id = :id",
        {"digest": _digest("replacement-occurrence"), "id": occurrence.id.uuid},
        "zoom occurrence lineage is immutable",
    )
    _assert_update_rejected(
        "UPDATE zoom_occurrence SET video_integration_id = :other WHERE id = :id",
        {"other": other_occurrence.video_integration_id.uuid, "id": occurrence.id.uuid},
        "zoom occurrence integration can only be detached",
    )
    _assert_update_rejected(
        "UPDATE zoom_capture_session SET doctor_id = :doctor, occurrence_id = :other "
        "WHERE id = :id",
        {
            "doctor": other.id.uuid,
            "other": other_occurrence.id.uuid,
            "id": capture.id.uuid,
        },
        "zoom capture session lineage is immutable",
    )
    _assert_update_rejected(
        "UPDATE zoom_capture_segment SET doctor_id = :doctor, "
        "capture_session_id = :other WHERE id = :id",
        {
            "doctor": other.id.uuid,
            "other": other_capture.id.uuid,
            "id": pending.id.uuid,
        },
        "zoom capture segment lineage is immutable",
    )


def test_only_one_open_encounter_is_allowed_per_occurrence() -> None:
    doctor = _doctor("one-open-encounter")
    occurrence = _occurrence(doctor, _integration(doctor, "one-open-encounter"))
    first = _capture_session(doctor, occurrence, 1)

    _assert_model_rejected(
        ZoomCaptureSession(
            doctor_id=doctor.id,
            occurrence_id=occurrence.id,
            sequence=2,
            lifecycle_state="open",
            assignment_state="unassigned",
            started_at=datetime.now(UTC),
        ),
        "uq_zoom_capture_open_occurrence",
    )

    first.lifecycle_state = "closed"
    first.ended_at = datetime.now(UTC)
    first.save()
    second = _capture_session(doctor, occurrence, 2)

    assert first.lifecycle_state == "closed"
    assert second.lifecycle_state == "open"
    assert (
        ZoomCaptureSession.where(
            ZoomCaptureSession.occurrence_id == occurrence.id,
            ZoomCaptureSession.lifecycle_state == "open",
        ).count()
        == 1
    )


def test_only_one_active_segment_is_allowed_per_encounter() -> None:
    doctor = _doctor("one-active-segment")
    occurrence = _occurrence(doctor, _integration(doctor, "one-active-segment"))
    capture_session = _capture_session(doctor, occurrence, 1)
    active = _segment(doctor, capture_session, 1, "recording")

    _assert_model_rejected(
        ZoomCaptureSegment(
            doctor_id=doctor.id,
            capture_session_id=capture_session.id,
            sequence=2,
            transport_state="starting",
            content_state="none",
            started_at=datetime.now(UTC),
        ),
        "uq_zoom_capture_segment_active_session",
    )

    assert active.transport_state == "recording"
    active.transport_state = "processing"
    active.stopped_at = datetime.now(UTC)
    active.save()
    resumed = _segment(doctor, capture_session, 2, "starting")
    assert resumed.transport_state == "starting"


def test_digest_only_tombstones_are_valid_after_crypto_erasure() -> None:
    doctor = _doctor("digest-only")
    now = datetime.now(UTC)
    integration = _integration(doctor, "digest-only")
    occurrence = _occurrence(
        doctor,
        integration,
        recall_bot_id_ciphertext="synthetic-bot-reference",
        recall_bot_id_digest=_digest("purged-bot"),
    )
    _assert_model_rejected(
        _occurrence(
            doctor,
            integration,
            save=False,
            clinical_room_id=occurrence.clinical_room_id,
            zoom_meeting_id_digest=occurrence.zoom_meeting_id_digest,
            recall_bot_id_ciphertext="synthetic-missing-digest",
        ),
        "bot_ciphertext_digest",
    )
    capture_session = _capture_session(doctor, occurrence, 1)
    for field in ("recall_recording_id_ciphertext", "recall_transcript_id_ciphertext"):
        _assert_model_rejected(
            _segment(
                doctor,
                capture_session,
                9,
                "complete",
                save=False,
                **{field: "synthetic-missing-digest"},
            ),
            "violates check constraint",
        )
    segment = _segment(
        doctor,
        capture_session,
        1,
        "complete",
        recall_recording_id_ciphertext="synthetic-recording-reference",
        recall_recording_id_digest=_digest("purged-recording"),
        recall_transcript_id_ciphertext="synthetic-transcript-reference",
        recall_transcript_id_digest=_digest("purged-transcript"),
    )

    occurrence.recall_bot_id_ciphertext = None
    occurrence.recall_bot_id_purged_at = now
    occurrence.save()
    segment.recall_recording_id_ciphertext = None
    segment.recall_recording_id_purged_at = now
    segment.recall_transcript_id_ciphertext = None
    segment.recall_transcript_id_purged_at = now
    segment.save()

    assert occurrence.recall_bot_id_ciphertext is None
    assert occurrence.recall_bot_id_digest is not None
    assert segment.recall_recording_id_ciphertext is None
    assert segment.recall_recording_id_digest is not None
    assert segment.recall_transcript_id_ciphertext is None
    assert segment.recall_transcript_id_digest is not None


def test_expired_unassigned_content_can_be_purged_but_not_retained() -> None:
    doctor = _doctor("purged-content")
    occurrence = _occurrence(doctor, _integration(doctor, "purged-content"))
    capture_session = _capture_session(doctor, occurrence, 1)
    now = datetime.now(UTC)
    expires_at = now - timedelta(minutes=1)
    pending = _segment(
        doctor,
        capture_session,
        1,
        "complete",
        started_at=now - timedelta(minutes=2),
        stopped_at=now - timedelta(minutes=1),
        content_state="pending_assignment",
        transcript_content_ciphertext="synthetic transcript content",
        transcript_content_digest=_digest("purged-content"),
        transcript_content_expires_at=expires_at,
    )
    with get_session() as session:
        raw_ciphertext = session.execute(
            text(
                "SELECT transcript_content_ciphertext FROM zoom_capture_segment "
                "WHERE id = :id"
            ),
            params={"id": pending.id.uuid},
        ).scalar_one()
    assert "synthetic transcript content" not in str(raw_ciphertext)
    pending.content_state = "purged"
    pending.transcript_content_ciphertext = None
    pending.transcript_content_purged_at = now
    purged = pending.save()

    assert purged.content_state == "purged"
    assert purged.transcript_content_ciphertext is None
    assert purged.transcript_content_digest is not None
    assert purged.transcript_content_expires_at == expires_at
    assert purged.transcript_content_purged_at == now

    _assert_model_rejected(
        ZoomCaptureSegment(
            doctor_id=doctor.id,
            capture_session_id=capture_session.id,
            sequence=2,
            transport_state="complete",
            content_state="pending_assignment",
            started_at=now,
            stopped_at=now,
            transcript_content_ciphertext="synthetic expired content",
            transcript_content_digest=_digest("invalid-expiry"),
            transcript_content_expires_at=expires_at,
        ),
        "zoom_capture_segment_ck_zoom_capture_segment_content_ex_",
    )


def test_downgrade_guard_serializes_prior_and_late_writers() -> None:
    migration = importlib.import_module(
        "migrations.versions.2026_09_01_f6ad12db8b0a_"
        "add_zoom_occurrence_capture_foundation"
    )
    engine = get_engine()
    with engine.connect() as connection:
        doctor_id = connection.execute(
            text("SELECT id FROM doctor LIMIT 1")
        ).scalar_one()
    writer = engine.connect()
    writer_transaction = writer.begin()
    _insert_room(writer, doctor_id, "prior-writer")
    guard_started = Event()

    def guard() -> None:
        with engine.connect() as connection, connection.begin():
            setattr(
                migration,
                "op",
                SimpleNamespace(
                    get_context=lambda: SimpleNamespace(as_sql=False),
                    get_bind=lambda: connection,
                ),
            )
            guard_started.set()
            migration._refuse_populated_downgrade()

    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked_guard = pool.submit(guard)
        assert guard_started.wait(timeout=2)
        time.sleep(0.2)
        assert not blocked_guard.done()
        writer_transaction.commit()
        writer.close()
        with pytest.raises(RuntimeError, match="blocked after use"):
            blocked_guard.result(timeout=5)

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM zoom_clinical_room"))

    lock_connection = engine.connect()
    lock_transaction = lock_connection.begin()
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: lock_connection,
        ),
    )
    migration._refuse_populated_downgrade()
    insert_started = Event()

    def late_insert() -> None:
        with engine.begin() as connection:
            insert_started.set()
            _insert_room(connection, doctor_id, "late-writer")

    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked_insert = pool.submit(late_insert)
        assert insert_started.wait(timeout=2)
        time.sleep(0.2)
        assert not blocked_insert.done()
        lock_transaction.rollback()
        lock_connection.close()
        blocked_insert.result(timeout=5)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM zoom_clinical_room"))
