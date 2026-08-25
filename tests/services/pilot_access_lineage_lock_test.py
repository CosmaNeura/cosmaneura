"""PostgreSQL serialization regressions for the pilot continuity lineage."""

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.doctor_account_closure as account_closure
import app.services.doctor_identity_rebind as identity_rebind
import app.services.pilot_access_grandfather as pilot_access
from app.services.clerk_cohort import ClerkCohortIdentity
from app.services.doctor_account_closure import DoctorAccountClosureError
from app.services.doctor_identity_rebind import DoctorIdentityRebindError
from app.services.pilot_access_lock import lock_pilot_access_lineage

from activemodel.session_manager import SessionManager
from sqlalchemy import text
from sqlmodel import Session


def test_lineage_lock_serializes_independent_database_transactions():
    engine = SessionManager.get_instance().get_engine()
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_lineage_lock() -> None:
        with Session(engine) as session:
            lock_pilot_access_lineage(session)
            holder_ready.set()
            assert release_holder.wait(timeout=5)
            session.commit()

    def wait_for_lineage_lock() -> None:
        with Session(engine) as session:
            lock_pilot_access_lineage(session)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_lineage_lock)
        assert holder_ready.wait(timeout=5)
        waiter = executor.submit(wait_for_lineage_lock)
        with pytest.raises(TimeoutError):
            waiter.result(timeout=0.2)
        release_holder.set()
        holder.result(timeout=5)
        waiter.result(timeout=5)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


class _FakeSession:
    def rollback(self) -> None:
        pass


@contextmanager
def _fake_session_context():
    yield _FakeSession()


def test_base_grant_takes_lineage_lock_before_subject_assessment(monkeypatch):
    calls: list[str] = []
    entries = [
        pilot_access.GrandfatherGrantInput(
            clerk_id=f"user_lock_order_grant_{index:02d}",
            email=f"lock-order-grant-{index:02d}@example.com",
        )
        for index in range(47)
    ]
    source_sha256 = pilot_access.canonical_clerk_email_pair_sha256(
        tuple(
            ClerkCohortIdentity(clerk_id=entry.clerk_id, email=entry.email)
            for entry in entries
        )
    )
    plan_sha256 = pilot_access.pilot_access_grant_plan_sha256(
        entries,
        source_ref="clerk-export:lock-order",
        source_sha256=source_sha256,
        operator_ref="change:lock-order",
        reason="Verify lineage lock order",
    )
    monkeypatch.setattr(
        pilot_access,
        "plan_pilot_access_grants",
        lambda *args, **kwargs: SimpleNamespace(plan_sha256=plan_sha256),
    )
    monkeypatch.setattr(pilot_access, "get_session", _fake_session_context)
    monkeypatch.setattr(
        pilot_access,
        "lock_pilot_access_lineage",
        lambda session: calls.append("lineage-lock"),
    )

    def stop_assessment(*args, **kwargs):
        del args, kwargs
        calls.append("subject-assessment")
        raise pilot_access.PilotAccessGrandfatherError("stop after lock-order check")

    monkeypatch.setattr(pilot_access, "_assess_grant_plan", stop_assessment)

    with pytest.raises(
        pilot_access.PilotAccessGrandfatherError, match="lock-order check"
    ):
        pilot_access.grant_pilot_access_grants(
            entries,
            source_ref="clerk-export:lock-order",
            source_sha256=source_sha256,
            operator_ref="change:lock-order",
            reason="Verify lineage lock order",
            confirm_plan_sha256=plan_sha256,
        )

    assert calls == ["lineage-lock", "subject-assessment"]


def test_base_revoke_takes_lineage_lock_before_subject_lock(monkeypatch):
    calls: list[str] = []
    clerk_id = "user_lock_order_revoke"
    reason = "Verify lineage lock order"
    plan_sha256 = pilot_access.pilot_access_revoke_plan_sha256(
        [clerk_id], operator_ref="change:lock-order", reason=reason
    )
    monkeypatch.setattr(pilot_access, "get_session", _fake_session_context)
    monkeypatch.setattr(
        pilot_access,
        "lock_pilot_access_lineage",
        lambda session: calls.append("lineage-lock"),
    )

    def stop_subject_lock(session, subject):
        del session, subject
        calls.append("subject-lock")
        raise pilot_access.PilotAccessGrandfatherError("stop after lock-order check")

    monkeypatch.setattr(pilot_access, "_lock_clerk_subject", stop_subject_lock)

    with pytest.raises(
        pilot_access.PilotAccessGrandfatherError, match="lock-order check"
    ):
        pilot_access.revoke_pilot_access_grants(
            [clerk_id],
            operator_ref="change:lock-order",
            reason=reason,
            confirm_plan_sha256=plan_sha256,
        )

    assert calls == ["lineage-lock", "subject-lock"]


@pytest.mark.parametrize(
    ("module", "function_name", "error_type", "arguments"),
    [
        (
            identity_rebind,
            "rebind_doctor_identity",
            DoctorIdentityRebindError,
            {
                "doctor_id": None,
                "stale_clerk_id": "user_lock_order_old",
                "replacement": ClerkCohortIdentity(
                    clerk_id="user_lock_order_new",
                    email="lock-order-rebind@example.com",
                ),
                "operator_ref": "change:lock-order",
                "reason": "Verify lineage lock order",
            },
        ),
        (
            account_closure,
            "close_doctor_account",
            DoctorAccountClosureError,
            {
                "doctor_id": None,
                "operator_ref": "change:lock-order",
                "reason": "Verify lineage lock order",
            },
        ),
    ],
)
def test_rebind_and_closure_take_lineage_lock_before_row_assessment(
    monkeypatch,
    module,
    function_name: str,
    error_type: type[ValueError],
    arguments: dict[str, Any],
):
    calls: list[str] = []
    session = _FakeSession()
    monkeypatch.setattr(
        module,
        "lock_pilot_access_lineage",
        lambda active_session: calls.append("lineage-lock"),
    )

    def stop_assessment(*args, **kwargs):
        del args, kwargs
        calls.append("row-assessment")
        raise error_type("stop after lock-order check")

    monkeypatch.setattr(module, "_evaluate", stop_assessment)
    function = getattr(module, function_name)

    with pytest.raises(error_type, match="lock-order check"):
        function(
            **arguments,
            confirmed_plan_sha256="a" * 64,
            session=session,
        )

    assert calls == ["lineage-lock", "row-assessment"]
