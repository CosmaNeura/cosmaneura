"""Request-owned SQL setup reuse with real PostgreSQL and fictional records."""

import time
from contextlib import contextmanager
from contextvars import copy_context
from threading import Thread

import pytest

from app.services import companion_records as records
from app.services import scribe_capture_deadline as capture
from app.utils import statement_timeout_scope as settings

from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

from tests.authority_kernel.scribe_current_timeout_setting_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_current_timeout_setting_test import (
    safe_query,
    setting_calls,
)

engine = _engine


@contextmanager
def actual_sql(connection):
    calls = []

    def observe(_connection, _cursor, statement, *_):
        calls.append(statement)

    event.listen(connection, "after_cursor_execute", observe)
    try:
        yield calls
    finally:
        event.remove(connection, "after_cursor_execute", observe)


@pytest.mark.parametrize("limit", [1, 99, 100, 101, 199, 200, 14999, 15000])
def test_tightening_is_positive_and_never_extends_current_limit(limit):
    result = settings.tighter_reusable_limits((limit, min(2000, limit)))
    assert all(
        0 < tightened <= fresh and fresh - tightened <= 99
        for tightened, fresh in zip(result, (limit, min(2000, limit)))
    )
    if limit < 100:
        assert result == (limit, limit)


def test_capture_only_reads_reuse_then_refresh_shorter_budget(engine, monkeypatch):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))  # Establish the actual PG transaction.
        budget = capture.CaptureBudget(30)
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(budget),
        ):
            for _ in range(5):
                assert connection.execute(safe_query()).all() == []
            assert len(calls) == 1
            assert 29800 <= calls[0][0] <= 30000 and calls[0][1] == 2000
            budget.expires_at = time.monotonic() + 0.15
            connection.execute(safe_query()).all()
            assert len(calls) == 2 and 0 < calls[-1][0] <= 150
            assert (
                settings.current_capture_timeout_scope(connection, budget) is not None
            )
        assert settings._capture_scopes.get() == ()
        connection.rollback()


def test_cancellation_after_capture_reuse_refuses_actual_query(engine, monkeypatch):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))  # Establish the actual PG transaction.
        budget = capture.CaptureBudget(30)
        with capture.capture_budget(budget), actual_sql(connection) as dispatched:
            connection.execute(safe_query()).all()
            original = capture.timeout_setting_in_force

            def cancel_after_check(*args):
                assert original(*args)
                budget.cancel()
                return True

            monkeypatch.setattr(capture, "timeout_setting_in_force", cancel_after_check)
            with pytest.raises(capture.CaptureDeadlineExceeded):
                connection.execute(safe_query()).all()
            assert len(dispatched) == 1
        connection.rollback()


@pytest.mark.parametrize(
    "boundary", ["commit", "rollback", "savepoint", "savepoint_rollback"]
)
def test_capture_transaction_boundaries_discard_settings(engine, monkeypatch, boundary):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
        ):
            connection.execute(safe_query()).all()
            connection.execute(safe_query()).all()
            assert len(calls) == 1
            if boundary in {"commit", "rollback"}:
                getattr(connection, boundary)()
                # The first SQL in a fresh PG transaction preserves the exact
                # setter; subsequent proven reads can use the tighter setting.
                connection.execute(text("SELECT 1"))
            else:
                nested = connection.begin_nested()
                connection.execute(safe_query()).all()
                getattr(nested, "commit" if boundary == "savepoint" else "rollback")()
            before = len(calls)
            connection.execute(safe_query()).all()
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
        connection.rollback()


def test_record_scope_and_nested_capture_never_keep_outer_setting(engine, monkeypatch):
    with engine.connect() as connection, Session(bind=connection) as session:
        outer = capture.CaptureBudget(30)
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(outer),
        ):
            connection.execute(safe_query()).all()
            with records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ):
                connection.execute(safe_query()).all()
                assert settings.current_capture_timeout_scope(connection, outer) is None
            before = len(calls)
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
            with capture.capture_budget(capture.CaptureBudget(15)):
                connection.execute(safe_query()).all()
                connection.execute(safe_query()).all()
                assert settings.current_capture_timeout_scope(connection, outer) is None
            before = len(calls)
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
        session.rollback()


def test_copied_thread_and_next_request_cannot_reuse_capture_scope(engine, monkeypatch):
    with engine.connect() as connection:
        first = capture.CaptureBudget(30)
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(first),
        ):
            connection.execute(safe_query()).all()
            assert len(calls) == 1
            scope = settings.current_capture_timeout_scope(connection, first)
            copied, seen = copy_context(), []
            thread = Thread(
                target=lambda: copied.run(
                    lambda: seen.append(
                        settings.current_capture_timeout_scope(connection, first)
                    )
                )
            )
            thread.start()
            thread.join()
            assert seen == [None]
        assert scope is not None and not scope.active and scope.setting is None
        assert (
            copied.run(
                lambda: settings.current_capture_timeout_scope(connection, first)
            )
            is None
        )
        for name in settings._TRANSACTION_EVENTS:
            assert not event.contains(connection, name, scope.invalidate)
        with capture.capture_budget(capture.CaptureBudget(30)):
            connection.execute(safe_query()).all()
        assert settings._capture_scopes.get() == ()
        connection.rollback()


def test_capture_failed_sql_rollbacks_and_uncertain_text_reset(engine, monkeypatch):
    with engine.connect() as connection:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
        ):
            connection.execute(safe_query()).all()
            connection.execute(
                text("SELECT set_config('statement_timeout', '0', true)")
            )
            before = len(calls)
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT 1/0"))
            connection.rollback()
            before = len(calls)
            connection.execute(safe_query()).all()
            assert len(calls) == before + 1
        connection.rollback()


def test_capture_expiry_and_slow_sql_do_not_escape_deadline(engine, monkeypatch):
    with engine.connect() as connection:
        budget = capture.CaptureBudget(0.06)
        with (
            capture.capture_budget(budget),
            setting_calls(connection, monkeypatch) as calls,
        ):
            connection.execute(safe_query()).all()
            # Text keeps the exact fresh limit and still receives PostgreSQL's
            # actual cancellation; tightening never grants additional time.
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT pg_sleep(0.15)"))
            assert calls[-1][0] <= 60 and calls[-1][0] > 0
        connection.rollback()


def test_capture_connection_scope_is_bounded_and_adapter_fails_closed(
    engine, monkeypatch
):
    with engine.connect() as connection:
        budget = capture.CaptureBudget(30)
        with capture.capture_budget(budget):
            assert settings.current_capture_timeout_scope(object(), budget) is None
            assert settings.current_capture_timeout_scope(connection, object()) is None
            monkeypatch.setattr(settings, "_MAX_CAPTURE_CONNECTIONS", 0)
            assert settings.current_capture_timeout_scope(connection, budget) is None
            with setting_calls(connection, monkeypatch) as calls:
                connection.execute(safe_query()).all()
                connection.execute(safe_query()).all()
                assert len(calls) == 2
        connection.rollback()
