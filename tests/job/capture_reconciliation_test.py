from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from app.celery import celery_app
from app.configuration.desktop_capture import DesktopCaptureSettings
from app.jobs import capture_reconciliation
from app.services.capture_coordination import (
    CaptureOutcomeUncertain,
    ScribeCaptureAuthorization,
    authorize_segment_start,
    create_authorized_capture_session,
)
from app.services.capture_lifecycle import (
    LocalSecretPurgeResult,
    NeverStartedReconciliationResult,
)
from app.services.capture_provider import (
    CaptureObservation,
    CaptureProviderOutcomeUncertain,
    CaptureProvisioning,
)

from activemodel.session_manager import get_session
from app.models.capture_coordination import (
    CaptureExternalResource,
    CaptureOperation,
    CaptureSegment,
)
from app.models.doctor import DoctorRole
from sqlmodel import select

from tests.factories import AppointmentFactory, DoctorFactory
from tests.routes.utils import provision_practice_in_force

NOW = datetime(2026, 8, 21, 18, 0, tzinfo=UTC)


@dataclass
class FakeProvider:
    create_error: BaseException | None = None
    find_error: BaseException | None = None
    observations: tuple[CaptureObservation, ...] = ()
    create_refs: list[str] = field(default_factory=list)
    find_refs: list[str] = field(default_factory=list)

    def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
        self.create_refs.append(correlation_ref)
        if self.create_error is not None:
            raise self.create_error
        return CaptureProvisioning(
            capture_container_ref="private_provider_container",
            capture_capability="private_provider_capability_0123456789",
            status="pending",
        )

    def find_captures(self, *, correlation_ref: str) -> tuple[CaptureObservation, ...]:
        self.find_refs.append(correlation_ref)
        if self.find_error is not None:
            raise self.find_error
        return self.observations


def _settings(
    *,
    enabled: bool,
    provider_create_enabled: bool | None = None,
    recovery_enabled: bool | None = None,
) -> DesktopCaptureSettings:
    return DesktopCaptureSettings(
        enabled=enabled,
        provider_create_enabled=(
            enabled if provider_create_enabled is None else provider_create_enabled
        ),
        recovery_enabled=(enabled if recovery_enabled is None else recovery_enabled),
        redirect_uris=(),
    )


def _prepared_scope():
    doctor = DoctorFactory.build(
        email=f"capture-worker-{uuid4()}@example.com",
        role=DoctorRole.normal,
        deleted_at=None,
    ).save()
    practice = provision_practice_in_force(doctor)
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        cancelled_at=None,
    ).save()
    authorization = ScribeCaptureAuthorization(
        consent_evidence_id=uuid4(),
        consent_policy_version="consent-v1",
        processing_session_id=uuid4(),
    )
    prepared = create_authorized_capture_session(
        actor_id=doctor.id,
        practice_id=practice.id,
        appointment_id=appointment.id,
        idempotency_key=f"session-{uuid4().hex}",
        authorization=authorization,
        now=NOW,
    )
    return doctor, practice, authorization, prepared


def _attempt_start(provider: FakeProvider, scope) -> None:
    doctor, practice, authorization, prepared = scope
    authorize_segment_start(
        provider=provider,
        practice_id=practice.id,
        actor_id=doctor.id,
        capture_session_id=prepared.capture_session_id,
        source_lease_id=prepared.source_lease_id,
        consent_evidence_id=authorization.consent_evidence_id,
        caller_principal="desktop-worker-test-principal",
        idempotency_key=f"segment-{uuid4().hex}",
        now=NOW + timedelta(seconds=5),
    )


def _operation_for_session(session_id):
    with get_session() as db:
        return db.exec(
            select(CaptureOperation).where(
                CaptureOperation.session_id == session_id,
                CaptureOperation.operation == "start_capture_segment",
            )
        ).one()


def test_stale_in_flight_operation_is_looked_up_without_create_or_late_capability():
    scope = _prepared_scope()
    prepared = scope[3]
    interrupted = FakeProvider(create_error=SystemExit("worker interrupted"))
    with pytest.raises(SystemExit):
        _attempt_start(interrupted, scope)

    recovery = FakeProvider(
        observations=(
            CaptureObservation(
                capture_container_ref="recovered_container",
                capture_capability="recovered_capability_0123456789",
                status="pending",
            ),
        )
    )
    result = capture_reconciliation.run_capture_maintenance(
        provider=recovery,
        settings=_settings(enabled=True),
        now=NOW + timedelta(minutes=4),
    )

    operation = _operation_for_session(prepared.capture_session_id)
    assert result.discovered == 1
    assert result.recovered == 0
    assert result.failed == 1
    assert recovery.find_refs == interrupted.create_refs
    assert recovery.create_refs == []
    assert operation.state == "failed"
    assert operation.last_error_code == "capture_authority_changed_after_provider"
    assert operation.attempt_count == 2
    assert operation.lease_expires_at is None
    with get_session() as db:
        segment = db.get(CaptureSegment, operation.segment_id)
        resources = db.exec(
            select(CaptureExternalResource).where(
                CaptureExternalResource.session_id == prepared.capture_session_id
            )
        ).all()
    assert segment is not None
    assert segment.capability_id is None
    assert segment.encrypted_capability is None
    assert len(resources) == 1


@pytest.mark.parametrize(
    ("enabled", "provider_create_enabled"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_recovery_disabled_never_builds_or_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    provider_create_enabled: bool,
) -> None:
    scope = _prepared_scope()
    prepared = scope[3]
    uncertain = FakeProvider(
        create_error=CaptureProviderOutcomeUncertain("private provider response")
    )
    with pytest.raises(CaptureOutcomeUncertain):
        _attempt_start(uncertain, scope)

    monkeypatch.setattr(
        capture_reconciliation,
        "_default_provider",
        lambda _settings: (_ for _ in ()).throw(
            AssertionError("provider must not be constructed")
        ),
    )
    result = capture_reconciliation.run_capture_maintenance(
        settings=_settings(
            enabled=enabled,
            provider_create_enabled=provider_create_enabled,
            recovery_enabled=False,
        ),
        now=NOW + timedelta(minutes=1),
    )

    operation = _operation_for_session(prepared.capture_session_id)
    assert result.enabled is False
    assert result.discovered == 0
    assert operation.state == "uncertain"
    assert operation.attempt_count == 1


@pytest.mark.parametrize("global_enabled", [False, True])
def test_recovery_can_run_while_new_provider_creation_is_disabled(
    global_enabled: bool,
) -> None:
    scope = _prepared_scope()
    prepared = scope[3]
    interrupted = FakeProvider(
        create_error=CaptureProviderOutcomeUncertain("private provider response")
    )
    with pytest.raises(CaptureOutcomeUncertain):
        _attempt_start(interrupted, scope)
    recovery = FakeProvider(
        observations=(
            CaptureObservation(
                capture_container_ref="recovered_container",
                capture_capability="recovered_capability_0123456789",
                status="pending",
            ),
        )
    )

    result = capture_reconciliation.run_capture_maintenance(
        provider=recovery,
        settings=_settings(
            enabled=global_enabled,
            provider_create_enabled=False,
            recovery_enabled=True,
        ),
        now=NOW + timedelta(minutes=1),
    )

    operation = _operation_for_session(prepared.capture_session_id)
    assert result.enabled is True
    assert result.recovered == 1
    assert operation.state == "succeeded"
    assert recovery.create_refs == []
    assert recovery.find_refs == interrupted.create_refs


def test_enabled_due_work_uses_injected_default_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope()
    uncertain = FakeProvider(
        create_error=CaptureProviderOutcomeUncertain("private provider response")
    )
    with pytest.raises(CaptureOutcomeUncertain):
        _attempt_start(uncertain, scope)

    recovery = FakeProvider(
        find_error=CaptureProviderOutcomeUncertain("private provider failure")
    )
    constructed: list[DesktopCaptureSettings] = []

    def default_provider(settings: DesktopCaptureSettings) -> FakeProvider:
        constructed.append(settings)
        return recovery

    monkeypatch.setattr(
        capture_reconciliation,
        "_default_provider",
        default_provider,
    )
    settings = _settings(enabled=True)
    result = capture_reconciliation.run_capture_maintenance(
        settings=settings,
        now=NOW + timedelta(minutes=1),
    )

    assert constructed == [settings]
    assert result.discovered == 1
    assert len(recovery.find_refs) == 1


def test_reconciliation_batch_is_bounded() -> None:
    session_ids = []
    for _ in range(3):
        scope = _prepared_scope()
        prepared = scope[3]
        uncertain = FakeProvider(
            create_error=CaptureProviderOutcomeUncertain("private provider response")
        )
        with pytest.raises(CaptureOutcomeUncertain):
            _attempt_start(uncertain, scope)
        session_ids.append(prepared.capture_session_id)

    recovery = FakeProvider(
        find_error=CaptureProviderOutcomeUncertain("private provider failure")
    )
    result = capture_reconciliation.run_capture_maintenance(
        provider=recovery,
        settings=_settings(enabled=True),
        now=NOW + timedelta(minutes=1),
        reconciliation_batch_size=2,
    )

    attempts = sorted(
        _operation_for_session(session_id).attempt_count for session_id in session_ids
    )
    assert result.discovered == 2
    assert result.deferred == 2
    assert len(recovery.find_refs) == 2
    assert attempts == [1, 2, 2]


def test_lookup_failure_returns_operation_to_bounded_retry_wait() -> None:
    scope = _prepared_scope()
    prepared = scope[3]
    uncertain = FakeProvider(
        create_error=CaptureProviderOutcomeUncertain("private provider response")
    )
    with pytest.raises(CaptureOutcomeUncertain):
        _attempt_start(uncertain, scope)

    attempted_at = NOW + timedelta(minutes=1)
    with capture_logs() as logs:
        result = capture_reconciliation.run_capture_maintenance(
            provider=FakeProvider(find_error=RuntimeError("private URL and body")),
            settings=_settings(enabled=True),
            now=attempted_at,
        )

    operation = _operation_for_session(prepared.capture_session_id)
    assert result.deferred == 1
    assert operation.state == "uncertain"
    assert operation.attempt_count == 2
    assert operation.next_attempt_at == attempted_at + timedelta(seconds=30)
    assert operation.lease_expires_at is None
    assert operation.last_error_code == "provider_reconciliation_unavailable"
    serialized = json.dumps(logs, sort_keys=True, default=str)
    for private_value in (
        "private URL and body",
        str(prepared.capture_session_id),
        uncertain.create_refs[0],
    ):
        assert private_value not in serialized


def test_due_cleanup_stays_blocked_visible_and_preserves_recovery_handle() -> None:
    scope = _prepared_scope()
    prepared = scope[3]
    provider = FakeProvider()
    _attempt_start(provider, scope)
    with get_session() as db:
        before = db.exec(
            select(CaptureExternalResource).where(
                CaptureExternalResource.session_id == prepared.capture_session_id
            )
        ).one()
        encrypted_ref = before.encrypted_external_ref
        ref_digest = before.external_ref_digest
        assert before.cleanup_due_at is not None
        due_at = before.cleanup_due_at

    with capture_logs() as logs:
        result = capture_reconciliation.run_capture_maintenance(
            settings=_settings(enabled=False),
            now=due_at + timedelta(seconds=1),
        )

    with get_session() as db:
        after = db.exec(
            select(CaptureExternalResource).where(
                CaptureExternalResource.session_id == prepared.capture_session_id
            )
        ).one()
    assert result.cleanup_newly_blocked == 1
    assert result.cleanup_blocked_due == 1
    assert after.cleanup_state == "failed"
    assert after.last_cleanup_error_code == "provider_delete_not_supported"
    assert after.cleanup_attempt_count == 0
    assert after.encrypted_external_ref == encrypted_ref
    assert after.external_ref_digest == ref_digest
    assert after.cleaned_at is None
    assert after.reference_purged_at is None

    serialized = json.dumps(logs, sort_keys=True, default=str)
    assert "capture_cleanup_blocked" in serialized
    for private_value in (
        str(prepared.capture_session_id),
        "private_provider_container",
        "private_provider_capability_0123456789",
    ):
        assert private_value not in serialized


def test_task_and_schedule_names_cannot_drift() -> None:
    assert "app.jobs.capture_reconciliation.perform" in celery_app.tasks
    entry = celery_app.conf.beat_schedule.get("capture_reconciliation")
    assert entry is not None
    assert entry["task"] == "app.jobs.capture_reconciliation.perform"


def test_cleanup_index_matches_due_scan() -> None:
    index = next(
        index
        for index in cast(Any, CaptureExternalResource).__table__.indexes
        if index.name == "ix_capture_resource_cleanup"
    )
    assert tuple(column.name for column in index.columns) == (
        "cleanup_state",
        "cleanup_due_at",
    )


def test_ambiguous_never_started_preparation_emits_count_only_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        capture_reconciliation,
        "purge_expired_capture_secrets",
        lambda **_kwargs: LocalSecretPurgeResult(0, 0),
    )
    monkeypatch.setattr(
        capture_reconciliation,
        "reconcile_never_started_leases",
        lambda **_kwargs: NeverStartedReconciliationResult(
            examined=3,
            closed=1,
            deferred=2,
        ),
    )
    monkeypatch.setattr(
        capture_reconciliation,
        "mark_due_cleanup_blocked",
        lambda **_kwargs: SimpleNamespace(newly_blocked=0, blocked_due=0),
    )

    with capture_logs() as logs:
        result = capture_reconciliation.run_capture_maintenance(
            settings=_settings(enabled=False),
            now=NOW,
        )

    assert result.never_started_deferred == 2
    warning = next(
        entry
        for entry in logs
        if entry.get("event_code") == "capture_never_started_reconciliation_deferred"
    )
    assert warning["examined"] == 3
    assert warning["deferred"] == 2
    assert set(warning) <= {"event", "event_code", "examined", "deferred", "log_level"}
