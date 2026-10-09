"""Actual native opening DBAPI-call counts, including raw timeout settings."""

import inspect
from collections import Counter
from contextlib import contextmanager

import psycopg
import pytest

from app.services import companion_records as records
from app.services import scribe_capture_deadline as deadline

from sqlalchemy import Engine, event

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


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_actual_native_uses_one_timeout_call_for_every_owned_statement(
    native, engine, kind, monkeypatch, record_property
):
    # Preserve this preceding round-trip optimization's isolated comparison.
    # Equal-setting reuse is qualified separately against the same SQL/locks.
    monkeypatch.setattr(deadline, "timeout_setting_in_force", lambda *_: False)
    monkeypatch.setattr(records, "timeout_setting_in_force", lambda *_: False)
    counts = Counter()

    class Cursor(psycopg.Cursor):
        def execute(self, query, *args, **kwargs):
            # Retain only fixed categories/counts, never SQL parameters or rows.
            value = query if isinstance(query, str) else ""
            if value.startswith("SET LOCAL statement_timeout") or value.startswith(
                "SET LOCAL lock_timeout"
            ):
                frame = inspect.currentframe()
                assert frame is not None and frame.f_back is not None
                try:
                    category = (
                        "capture_timeout_calls"
                        if frame.f_back.f_code.co_name
                        in {"before_capture_statement", "original_settings"}
                        else "record_timeout_calls"
                    )
                    counts[category] += 1
                finally:
                    del frame
            elif value.startswith("SELECT set_config('statement_timeout'"):
                frame = inspect.currentframe()
                assert frame is not None and frame.f_back is not None
                try:
                    category = (
                        "capture_timeout_calls"
                        if frame.f_back.f_code.co_name
                        in {"before_capture_statement", "original_settings"}
                        else "record_timeout_calls"
                    )
                    counts[category] += 1
                finally:
                    del frame
            else:
                counts["other_calls"] += 1
            return super().execute(query, *args, **kwargs)

    def attach(connection, *_args):
        connection.cursor_factory = Cursor

    def original_settings(connection, cursor, *_args):
        budget = deadline._sql_budget()
        if budget is None:
            return
        if connection.dialect.name != "postgresql":
            raise deadline.CaptureDeadlineExceeded("capture_database_unavailable")
        milliseconds = max(1, int(budget.check() * 1000))
        cursor.execute(f"SET LOCAL statement_timeout = {milliseconds}")
        milliseconds = max(1, min(2000, int(budget.check() * 1000)))
        cursor.execute(f"SET LOCAL lock_timeout = {milliseconds}")
        budget.check()

    @contextmanager
    def original_record_settings(session, record_deadline):
        records.check_deadline(record_deadline)
        connection = session.connection()
        assert connection.dialect.name == "postgresql"

        def before_cursor_execute(_connection, cursor, *_args):
            remaining = records.check_deadline(record_deadline)
            cursor.execute(f"SET LOCAL statement_timeout = {remaining}")
            remaining = records.check_deadline(record_deadline)
            cursor.execute(f"SET LOCAL lock_timeout = {min(remaining, 2000)}")
            records.check_deadline(record_deadline)

        event.listen(connection, "before_cursor_execute", before_cursor_execute)
        try:
            records.check_deadline(record_deadline)
            yield
        finally:
            event.remove(connection, "before_cursor_execute", before_cursor_execute)

    def statements(_connection, _cursor, *_args):
        if deadline._sql_budget() is not None:
            counts["owned_statements"] += 1

    engine.dispose()
    event.listen(engine, "connect", attach)
    event.listen(engine, "before_cursor_execute", statements)
    results = []
    try:
        for optimized in (False, True):
            counts.clear()
            if not optimized:
                event.remove(
                    Engine, "before_cursor_execute", deadline.before_capture_statement
                )
                event.listen(Engine, "before_cursor_execute", original_settings)
            try:
                with monkeypatch.context() as patch:
                    if not optimized:
                        patch.setattr(
                            "app.services.scribe_encounters.transaction_budget",
                            original_record_settings,
                        )
                    response = native.post(kind)
            finally:
                if not optimized:
                    event.remove(Engine, "before_cursor_execute", original_settings)
                    event.listen(
                        Engine,
                        "before_cursor_execute",
                        deadline.before_capture_statement,
                    )
            assert response.status_code == 200
            # Query reductions may lower the total; every remaining statement
            # must still receive the same timeout protection below.
            assert counts["owned_statements"] > 0
            assert counts["capture_timeout_calls"] + (
                counts["record_timeout_calls"] if optimized else 0
            ) == counts["owned_statements"] * (1 if optimized else 2)
            results.append(dict(counts))
    finally:
        event.remove(engine, "connect", attach)
        event.remove(engine, "before_cursor_execute", statements)
        engine.dispose()
    assert results[0]["owned_statements"] == results[1]["owned_statements"]
    assert results[0]["other_calls"] == results[1]["other_calls"]
    assert results[0]["record_timeout_calls"] == 2 * results[1]["record_timeout_calls"]
    record_property("operation", kind)
    record_property(
        "original_calls",
        sum(
            results[0][key]
            for key in ("capture_timeout_calls", "record_timeout_calls", "other_calls")
        ),
    )
    record_property(
        "optimized_calls",
        sum(
            results[1][key]
            for key in ("capture_timeout_calls", "record_timeout_calls", "other_calls")
        ),
    )
    record_property("owned_statements", results[1]["owned_statements"])
