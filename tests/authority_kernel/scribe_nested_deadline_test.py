"""Both independent deadlines on actual SQL and actual native opening routes."""

import inspect
import time
from collections import Counter
from contextvars import copy_context
from threading import Thread
from types import SimpleNamespace

import psycopg
import pytest

from app.services import scribe_capture_deadline as capture
from app.services.companion_records import (
    RecordBudget,
    RecordDeadline,
    transaction_budget,
)
from app.utils.statement_timeout_scope import (
    acknowledge_record_timeout,
    consume_record_timeout,
    current_record_timeout_scope,
    record_timeout_scope,
)

from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

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


def test_correspondence_is_one_statement_and_exact_scope_connection_thread():
    connection, other = object(), object()
    budget = capture.CaptureBudget(1)
    context = SimpleNamespace()
    with record_timeout_scope(connection, lambda: (1000, 1000)) as scope:
        assert current_record_timeout_scope(connection) is scope
        assert current_record_timeout_scope(other) is None
        seen = []
        copied = copy_context()
        thread = Thread(
            target=lambda: copied.run(
                lambda: seen.append(current_record_timeout_scope(connection))
            )
        )
        thread.start()
        thread.join()
        assert seen == [None]
        acknowledge_record_timeout(context, scope, budget, "record")
        assert not consume_record_timeout(context, scope, object(), "record")
        assert not consume_record_timeout(context, scope, budget, "capture")
        assert consume_record_timeout(context, scope, budget, "record")
        assert not consume_record_timeout(context, scope, budget, "record")
        with record_timeout_scope(connection, lambda: (500, 500)):
            assert current_record_timeout_scope(connection) is None
        assert current_record_timeout_scope(connection) is scope
        acknowledge_record_timeout(context, scope, budget, "record")
    assert current_record_timeout_scope(connection) is None
    assert not consume_record_timeout(context, scope, budget, "record")
    with record_timeout_scope(connection, lambda: (1000, 1000)) as next_scope:
        assert not consume_record_timeout(context, next_scope, budget, "record")


@pytest.mark.parametrize("first", ["capture", "record"])
@pytest.mark.parametrize("cancelled", [None, "capture", "record"])
def test_both_callback_orders_check_both_budgets_after_the_one_setting(
    monkeypatch, first, cancelled
):
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session = Session()
    monkeypatch.setattr(session, "connection", lambda: connection)
    listeners = []
    monkeypatch.setattr(
        "app.services.companion_records.event.listen",
        lambda _c, _n, callback: listeners.append(callback),
    )
    monkeypatch.setattr(
        "app.services.companion_records.event.remove", lambda *_args: None
    )
    budget = capture.CaptureBudget(1)
    record = RecordBudget(time.monotonic() + 2)
    sent = []

    def setting(_query, values):
        sent.append(values)
        if cancelled is not None:
            (budget if cancelled == "capture" else record).cancel()

    with capture.capture_budget(budget), transaction_budget(session, record):
        callbacks = [capture.before_capture_statement, listeners[0]]
        if first == "record":
            callbacks.reverse()
        context = SimpleNamespace()

        def dispatch():
            for callback in callbacks:
                callback(
                    connection,
                    SimpleNamespace(execute=setting),
                    "SELECT 1",
                    None,
                    context,
                    False,
                )

        if cancelled is not None:
            with pytest.raises((capture.CaptureDeadlineExceeded, RecordDeadline)):
                dispatch()
        else:
            dispatch()
        assert len(sent) == 1
        assert 0 < int(sent[0][0]) <= 1000


@pytest.mark.parametrize("first", ["capture", "record"])
def test_combined_setting_failure_never_admits_sql(monkeypatch, first):
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session = Session()
    monkeypatch.setattr(session, "connection", lambda: connection)
    listeners = []
    monkeypatch.setattr(
        "app.services.companion_records.event.listen",
        lambda _c, _n, callback: listeners.append(callback),
    )
    monkeypatch.setattr(
        "app.services.companion_records.event.remove", lambda *_args: None
    )

    def setting(_query, _values):
        raise RuntimeError("synthetic_timeout_setting_failure")

    with (
        capture.capture_budget(capture.CaptureBudget(1)),
        transaction_budget(session, RecordBudget(time.monotonic() + 2)),
        pytest.raises(RuntimeError, match="synthetic_timeout_setting_failure"),
    ):
        callbacks = [capture.before_capture_statement, listeners[0]]
        if first == "record":
            callbacks.reverse()
        for callback in callbacks:
            callback(
                connection,
                SimpleNamespace(execute=setting),
                "SELECT 1",
                None,
                SimpleNamespace(),
                False,
            )
        pytest.fail("Actual SQL cannot be admitted after settings failure")


@pytest.mark.parametrize("shorter", ["capture", "record"])
@pytest.mark.parametrize("ending", ["commit", "rollback"])
def test_both_limits_decrease_and_reset_on_actual_connection(engine, shorter, ending):
    with engine.connect() as connection, Session(bind=connection) as session:
        defaults = connection.execute(
            text(
                "SELECT current_setting('statement_timeout'), "
                "current_setting('lock_timeout')"
            )
        ).one()
        connection.rollback()
        capture_seconds, record_seconds = (1, 3) if shorter == "capture" else (3, 1)
        with (
            capture.capture_budget(capture.CaptureBudget(capture_seconds)),
            transaction_budget(
                session, RecordBudget(time.monotonic() + record_seconds)
            ),
        ):
            query = text(
                "SELECT current_setting('statement_timeout'), "
                "current_setting('lock_timeout')"
            )
            first = connection.execute(query).one()
            time.sleep(0.03)
            second = connection.execute(query).one()
            assert (
                0
                < float(second[0].removesuffix("ms"))
                < float(first[0].removesuffix("ms"))
                <= 1000
            )
            assert first[0] == first[1] and second[0] == second[1]
            getattr(session, ending)()
        assert connection.execute(query).one() == defaults
        connection.rollback()


@pytest.mark.parametrize("shorter", ["capture", "record"])
def test_shorter_deadline_cancels_actual_slow_sql(engine, shorter):
    capture_seconds, record_seconds = (0.08, 1) if shorter == "capture" else (1, 0.08)
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            capture.capture_budget(capture.CaptureBudget(capture_seconds)),
            transaction_budget(
                session, RecordBudget(time.monotonic() + record_seconds)
            ),
        ):
            started = time.monotonic()
            with pytest.raises(DBAPIError) as error:
                connection.execute(text("SELECT pg_sleep(1)"))
            assert getattr(error.value.orig, "sqlstate", None) == "57014"
            assert time.monotonic() - started < 0.5
            time.sleep(0.01)
            with pytest.raises((capture.CaptureDeadlineExceeded, RecordDeadline)):
                connection.execute(text("SELECT 1"))
        session.rollback()
        assert connection.scalar(text("SELECT 1")) == 1
        connection.rollback()


@pytest.mark.parametrize("cancelled", ["capture", "record"])
def test_cancellation_after_combined_setting_refuses_actual_sql(
    engine, cancelled, monkeypatch
):
    dispatched = []
    capture_budget = capture.CaptureBudget(2)
    record_budget = RecordBudget(time.monotonic() + 2)

    class Cursor(psycopg.Cursor):
        def execute(self, query, *args, **kwargs):
            if isinstance(query, str) and query.startswith(
                "SELECT set_config('statement_timeout'"
            ):
                result = super().execute(query, *args, **kwargs)
                (capture_budget if cancelled == "capture" else record_budget).cancel()
                return result
            dispatched.append(True)
            return super().execute(query, *args, **kwargs)

    with engine.connect() as connection, Session(bind=connection) as session:
        raw = connection.connection.driver_connection
        with monkeypatch.context() as patch:
            patch.setattr(raw, "cursor_factory", Cursor)
            with (
                capture.capture_budget(capture_budget),
                transaction_budget(session, record_budget),
                pytest.raises((capture.CaptureDeadlineExceeded, RecordDeadline)),
            ):
                connection.execute(text("SELECT 1"))
        assert dispatched == []
        session.rollback()
        assert connection.scalar(text("SELECT 1")) == 1
        connection.rollback()


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_real_native_removes_only_duplicate_setting_calls(
    native, engine, kind, monkeypatch, record_property
):
    # Isolate the original two-budget correspondence; equal-setting reuse has
    # separate actual-PostgreSQL coverage and is not part of this comparison.
    monkeypatch.setattr(capture, "timeout_setting_in_force", lambda *_: False)
    monkeypatch.setattr(
        "app.services.companion_records.timeout_setting_in_force", lambda *_: False
    )
    counts = Counter()

    class Cursor(psycopg.Cursor):
        def execute(self, query, *args, **kwargs):
            value = query if isinstance(query, str) else ""
            if value.startswith("SELECT set_config('statement_timeout'"):
                frame = inspect.currentframe()
                assert frame is not None and frame.f_back is not None
                try:
                    category = (
                        "capture"
                        if frame.f_back.f_code.co_name == "before_capture_statement"
                        else "record"
                    )
                    counts[category] += 1
                finally:
                    del frame
            else:
                counts["other"] += 1
            return super().execute(query, *args, **kwargs)

    def attach(connection, *_args):
        connection.cursor_factory = Cursor

    def statement(_connection, _cursor, *_args):
        if capture._sql_budget() is not None:
            counts["owned"] += 1

    engine.dispose()
    event.listen(engine, "connect", attach)
    event.listen(engine, "before_cursor_execute", statement)
    results = []
    try:
        for combined in (False, True):
            counts.clear()
            with monkeypatch.context() as patch:
                if not combined:
                    patch.setattr(
                        "app.services.companion_records.current_record_timeout_scope",
                        lambda _: None,
                    )
                    patch.setattr(
                        capture, "current_record_timeout_scope", lambda _: None
                    )
                response = native.post(kind)
            assert response.status_code == 200
            # The invariant is timeout coverage, independent of query volume.
            assert counts["owned"] > 0
            assert (
                counts["capture"] + (counts["record"] if combined else 0)
                == counts["owned"]
            )
            results.append(dict(counts))
    finally:
        event.remove(engine, "connect", attach)
        event.remove(engine, "before_cursor_execute", statement)
        engine.dispose()
    assert results[0]["owned"] == results[1]["owned"]
    assert results[0]["other"] == results[1]["other"]
    assert results[0]["record"] == results[1]["record"] > 0
    assert results[0]["capture"] - results[1]["capture"] == results[1]["record"]
    record_property("operation", kind)
    for name, result in zip(("separate", "combined"), results, strict=True):
        record_property(
            name + "_calls",
            sum(result.get(key, 0) for key in ("capture", "record", "other")),
        )
    record_property("unchanged_authority_statements", results[1]["owned"])
