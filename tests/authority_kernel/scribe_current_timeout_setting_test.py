"""Actual PostgreSQL setting coverage, cancellation and transaction isolation."""

import time
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
import pytest

from app.services import companion_records as records
from app.services import scribe_capture_deadline as capture
from app.utils import statement_timeout_scope as settings

from app.models.doctor import Doctor
from sqlalchemy import event, func, literal_column, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


@contextmanager
def setting_calls(connection, monkeypatch):
    calls = []

    class Cursor(psycopg.Cursor):
        def execute(self, query, *args, **kwargs):
            if query == (
                "SELECT set_config('statement_timeout', %s, true), "
                "set_config('lock_timeout', %s, true)"
            ):
                calls.append(tuple(map(int, args[0])))
            return super().execute(query, *args, **kwargs)

    raw = connection.connection.driver_connection
    with monkeypatch.context() as patch:
        patch.setattr(raw, "cursor_factory", Cursor)
        yield calls


def safe_query():
    return select(Doctor.id).where(Doctor.clerk_id == "user_invented_timeout_absent")


@pytest.mark.parametrize("cancelled", ["capture", "record"])
def test_cancellation_after_reuse_decision_still_refuses_actual_sql(
    engine, monkeypatch, cancelled
):
    original = capture.timeout_setting_in_force
    with engine.connect() as connection, Session(bind=connection) as session:
        budget = capture.CaptureBudget(30)
        record = records.RecordBudget(time.monotonic() + 60)
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(budget),
            records.transaction_budget(session, record),
        ):
            connection.execute(safe_query()).all()

            def cancelled_reuse(*args):
                reused = original(*args)
                assert reused
                (budget if cancelled == "capture" else record).cancel()
                return reused

            # Either callback can own the setting; the other consumes its
            # per-statement acknowledgement. Exercise the actual owner.
            monkeypatch.setattr(capture, "timeout_setting_in_force", cancelled_reuse)
            monkeypatch.setattr(records, "timeout_setting_in_force", cancelled_reuse)
            with pytest.raises(
                (capture.CaptureDeadlineExceeded, records.RecordDeadline)
            ):
                connection.execute(safe_query()).all()
            assert calls == [(15000, 2000)]
        session.rollback()


def test_another_connection_never_reuses_setting(engine, monkeypatch):
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            connection.execute(safe_query()).all()
            with engine.connect() as other:
                with setting_calls(other, monkeypatch) as other_calls:
                    other.execute(safe_query()).all()
                    other.execute(safe_query()).all()
                    assert len(other_calls) == 2
                other.rollback()
            connection.execute(safe_query()).all()
            assert calls == [(15000, 2000)]
        session.rollback()


def test_exact_cap_setting_stays_and_next_shorter_budget_refreshes(engine, monkeypatch):
    with engine.connect() as connection, Session(bind=connection) as session:
        budget = capture.CaptureBudget(30)
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(budget),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            for _ in range(5):
                assert connection.execute(safe_query()).all() == []
            assert calls == [(15000, 2000)]
            budget.expires_at = time.monotonic() + 1
            connection.execute(safe_query()).all()
            assert len(calls) == 2 and 0 < calls[-1][0] <= 1000
            assert calls[-1][1] <= 1000
        session.rollback()
    assert settings._scopes.get() == ()


@pytest.mark.parametrize("cancelled", ["capture", "record"])
def test_cancelled_budget_refuses_before_reusing_setting(
    engine, monkeypatch, cancelled
):
    with engine.connect() as connection, Session(bind=connection) as session:
        budget, record = (
            capture.CaptureBudget(30),
            records.RecordBudget(time.monotonic() + 60),
        )
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(budget),
            records.transaction_budget(session, record),
        ):
            connection.execute(safe_query()).all()
            (budget if cancelled == "capture" else record).cancel()
            with pytest.raises(
                (capture.CaptureDeadlineExceeded, records.RecordDeadline)
            ):
                connection.execute(safe_query()).all()
            assert calls == [(15000, 2000)]
        session.rollback()


@pytest.mark.parametrize(
    "boundary", ["commit", "rollback", "savepoint", "savepoint_rollback"]
)
def test_transaction_changes_invalidate_setting(engine, monkeypatch, boundary):
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            connection.execute(safe_query()).all()
            connection.execute(safe_query()).all()
            assert len(calls) == 1
            if boundary in {"commit", "rollback"}:
                getattr(connection, boundary)()
            else:
                nested = connection.begin_nested()
                connection.execute(safe_query()).all()
                assert len(calls) == 3  # SAVEPOINT text and first nested read.
                getattr(nested, "commit" if boundary == "savepoint" else "rollback")()
            before = len(calls)
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
        session.rollback()


@pytest.mark.parametrize(
    "statement", ["text", "function", "literal", "mutation", "mutating_cte"]
)
def test_uncertain_statements_invalidate_prior_settings(engine, monkeypatch, statement):
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            connection.execute(safe_query()).all()
            if statement == "text":
                query = text("SELECT set_config('statement_timeout', '0', true)")
            elif statement == "function":
                query = (
                    select(func.set_config("statement_timeout", "0", True))
                    .select_from(Doctor)
                    .limit(1)
                )
            elif statement == "literal":
                query = (
                    select(literal_column("set_config('statement_timeout', '0', true)"))
                    .select_from(Doctor)
                    .limit(1)
                )
            else:
                query = (
                    update(Doctor)
                    .where(Doctor.clerk_id == "user_invented_timeout_absent")
                    .values(last_active_at=datetime.now(UTC))
                )
                if statement == "mutating_cte":
                    query = select(query.returning(Doctor.id).cte("changed_doctors"))
            connection.execute(query)
            before = len(calls)
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
        session.rollback()


def test_nested_record_scopes_invalidate_outer_correspondence(engine, monkeypatch):
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            connection.execute(safe_query()).all()
            assert len(calls) == 1
            with records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 45)
            ):
                connection.execute(safe_query()).all()
                assert len(calls) == 4
            connection.execute(safe_query()).all()
            assert len(calls) == 5
        session.rollback()


def test_failed_sql_and_rollback_reset_setting(engine, monkeypatch):
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            connection.execute(safe_query()).all()
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT 1/0"))
            connection.rollback()
            before = len(calls)
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
        session.rollback()


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_native_retains_receipts_exact_locks_and_sql_with_fewer_setters(
    native, engine, monkeypatch, kind, record_property
):
    results = []
    for previous in (True, False):
        counts = {"setters": 0, "sql": 0}
        locks = []

        class Cursor(psycopg.Cursor):
            def execute(self, query, *args, **kwargs):
                if isinstance(query, str) and query.startswith(
                    "SELECT set_config('statement_timeout'"
                ):
                    counts["setters"] += 1
                else:
                    counts["sql"] += 1
                return super().execute(query, *args, **kwargs)

        def attach(connection, *_):
            connection.cursor_factory = Cursor

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        engine.dispose()
        event.listen(engine, "connect", attach)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            with monkeypatch.context() as patch:
                if previous:
                    patch.setattr(capture, "timeout_setting_in_force", lambda *_: False)
                    patch.setattr(records, "timeout_setting_in_force", lambda *_: False)
                response = native.post(kind)
        finally:
            event.remove(engine, "connect", attach)
            event.remove(engine, "before_cursor_execute", observe)
            engine.dispose()
        assert response.status_code == 200
        result = response.json()["result"]
        receipt = result["encounter"] if kind == "mount" else result["receipt"]
        results.append((receipt, counts, locks))
        assert engine.pool.checkedout() == 0 and settings._scopes.get() == ()
    assert results[0][0] == results[1][0] and results[0][2] == results[1][2]
    assert results[0][1]["sql"] == results[1][1]["sql"]
    assert results[0][1]["setters"] > results[1][1]["setters"]
    record_property(
        "setters_removed", results[0][1]["setters"] - results[1][1]["setters"]
    )
    record_property("exact_lock_sequence_preserved", True)
