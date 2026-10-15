"""Expire only replay payloads; expose queue signals without record content."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from typeid import TypeID

import app.jobs.session_prep as job
import app.services.session_prep_operations as operations
from app.configuration.session_prep import SessionPrepProviderSettings

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.session_prep_runtime import SessionPrepBuild, SessionPrepReadReceipt
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import tests.services.session_prep_runtime_test as runtime_tests

scope = runtime_tests.scope


def test_expiry_is_bounded_preserves_live_receipts_and_retains_audit(
    scope, monkeypatch
):
    _doctor, _target, _note, token, record = scope
    runtime_tests._read(token, record, runtime_tests._action(token))
    recent = SessionPrepReadReceipt.first()
    assert recent is not None
    audit_count = AuthorityAuditEvent.count()
    old_time = datetime.now(UTC) - timedelta(days=2)
    with get_session() as session:
        for i in range(4):
            identifier = cast(TypeIDType, TypeID("prep_read"))
            payload = {
                **recent.response_payload,
                "tool_call_ref": str(identifier),
                "recorded_at": old_time.isoformat(),
            }
            receipt = SessionPrepReadReceipt(
                workload_client_id=recent.workload_client_id,
                interaction_id=recent.interaction_id,
                idempotency_key=f"synthetic-expired-key-{i}",
                request_digest=recent.request_digest,
                record_scope_id=record.id,
                response_payload=payload,
                recorded_at=old_time,
                retain_until=old_time + timedelta(hours=24),
            )
            setattr(receipt, "id", identifier)
            session.add(receipt)
        session.commit()
    monkeypatch.setattr(operations, "RECEIPT_CLEANUP_BATCH", 3)
    assert operations.expire_read_receipts() == 3
    assert operations.expire_read_receipts() == 1
    assert operations.expire_read_receipts() == 0
    assert SessionPrepReadReceipt.count() == 1
    assert AuthorityAuditEvent.count() == audit_count
    with get_session() as session:
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM session_prep_read_receipt"))
        session.rollback()


def test_queue_health_reports_only_counts_and_detects_stuck_work(scope):
    build = SessionPrepBuild.first()
    assert build is not None
    build.requested_at = datetime.now(UTC) - timedelta(minutes=3)
    build.save()
    health = operations.runtime_health()
    assert health["pending"] == 1
    assert health["status"] == "degraded"
    oldest_pending_seconds = health["oldest_pending_seconds"]
    assert isinstance(oldest_pending_seconds, int)
    assert oldest_pending_seconds >= 180
    assert set(health) == {
        "pending",
        "running",
        "ready",
        "failed",
        "actionable_failed",
        "oldest_pending_seconds",
        "status",
    }
    assert "context_prep" not in json.dumps(health)


@pytest.mark.parametrize(
    "failure_code,age,expected_status",
    [
        ("scope_unavailable", timedelta(0), "degraded"),
        ("scope_unavailable", timedelta(hours=1), "ok"),
        ("no_eligible_sources", timedelta(0), "ok"),
        ("generation_failed", timedelta(hours=1), "ok"),
        ("generation_failed", timedelta(0), "degraded"),
        ("invalid_brief", timedelta(0), "degraded"),
        (None, timedelta(0), "degraded"),
    ],
)
def test_health_distinguishes_recent_outages_from_terminal_scope_changes(
    scope, failure_code, age, expected_status
):
    build = SessionPrepBuild.first()
    assert build is not None
    build.state = "failed"
    build.failure_code = failure_code
    build.retry_at = datetime.now(UTC) - age
    build.save()
    health = operations.runtime_health()
    assert health["failed"] == 1
    assert health["actionable_failed"] == int(expected_status == "degraded")
    assert health["status"] == expected_status


def test_maintenance_expires_receipts_even_when_generation_is_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        job.configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(),
    )
    monkeypatch.setattr(
        job, "expire_read_receipts", lambda: calls.append("expired") or 1
    )
    monkeypatch.setattr(job, "runtime_health", lambda: {"status": "ok"})
    job.maintain()
    assert calls == ["expired"]


def test_maintenance_failure_emits_no_exception_or_record_detail(monkeypatch):
    messages = []

    def fail():
        raise RuntimeError("synthetic-private-exception-sentinel")

    monkeypatch.setattr(job, "expire_read_receipts", fail)
    monkeypatch.setattr(
        job.log, "warning", lambda *args, **kwargs: messages.append((args, kwargs))
    )
    job.maintain()
    assert "synthetic-private" not in json.dumps(messages)
    assert messages[0][1] == {"event_code": "session_prep_maintenance_failed"}


@pytest.mark.parametrize("drift", ["disabled", "wrong_issuer"])
def test_worker_configuration_failure_remains_visible_in_health(
    scope, drift, monkeypatch
):
    from app.services.session_prep_worker import run_one_build

    # Worker completion and health's alert window must use the same clock;
    # collection-time NOW can be hours old by this point in the full suite.
    monkeypatch.setattr(operations, "datetime", runtime_tests._AuthorityClock)
    settings = replace(
        runtime_tests.AUTHORITY_SETTINGS,
        **(
            {"enabled": False}
            if drift == "disabled"
            else {"clerk_issuer": "https://wrong.invalid"}
        ),
    )
    assert (
        run_one_build(
            generate=runtime_tests._brief,
            clock=lambda: runtime_tests.NOW,
            settings=settings,
        )
        == "failed"
    )
    build = SessionPrepBuild.first()
    assert build is not None
    assert build.state == "failed"
    assert build.failure_code == "scope_unavailable"
    assert operations.runtime_health()["status"] == "degraded"
