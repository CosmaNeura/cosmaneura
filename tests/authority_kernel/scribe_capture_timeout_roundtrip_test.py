"""Actual transaction-local timeout behavior on the owned synthetic lab."""

import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.services.companion_records import (
    RecordBudget,
    RecordDeadline,
    transaction_budget,
)
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    before_capture_statement,
    capture_budget,
)

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

from tests.authority_kernel.scribe_encounters_test import engine as _engine

engine = _engine


@contextmanager
def guard(connection, kind, seconds):
    if kind == "capture":
        with capture_budget(CaptureBudget(seconds)):
            yield
    else:
        with (
            Session(bind=connection) as session,
            transaction_budget(session, RecordBudget(time.monotonic() + seconds)),
        ):
            yield


@pytest.mark.parametrize("kind", ["capture", "record"])
@pytest.mark.parametrize("ending", ["commit", "rollback"])
def test_both_settings_are_local_and_refreshed_on_each_actual_statement(
    engine, kind, ending
):
    with engine.connect() as connection:
        defaults = connection.execute(
            text(
                "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
            )
        ).one()
        connection.rollback()
        with guard(connection, kind, 1):
            first = connection.execute(
                text(
                    "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
                )
            ).one()
            time.sleep(0.03)
            second = connection.execute(
                text(
                    "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
                )
            ).one()
            assert (
                0
                < float(second[0].removesuffix("ms"))
                < float(first[0].removesuffix("ms"))
                <= 1000
            )
            assert second[0] == second[1]
            getattr(connection, ending)()
        assert (
            connection.execute(
                text(
                    "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
                )
            ).one()
            == defaults
        )
        connection.rollback()


@pytest.mark.parametrize("kind", ["capture", "record"])
def test_actual_slow_statement_is_cancelled_before_next_sql(engine, kind):
    with engine.connect() as connection:
        with guard(connection, kind, 0.08):
            started = time.monotonic()
            with pytest.raises(DBAPIError) as error:
                connection.execute(text("SELECT pg_sleep(1)"))
            assert getattr(error.value.orig, "sqlstate", None) == "57014"
            assert time.monotonic() - started < 0.5
            with pytest.raises((CaptureDeadlineExceeded, RecordDeadline)):
                connection.execute(text("SELECT 1"))
        connection.rollback()
        assert connection.scalar(text("SELECT 1")) == 1


@pytest.mark.parametrize("kind", ["capture", "record"])
def test_actual_lock_wait_is_bounded_and_does_not_mutate(engine, kind):
    with engine.connect() as holder, engine.connect() as waiting:
        # A session advisory lock supplies a synthetic contested resource with
        # no persistent rows or application ownership changes.
        holder.execute(text("SELECT pg_advisory_lock(55937007)"))
        try:
            with guard(waiting, kind, 3):
                configured = waiting.execute(
                    text("SELECT current_setting('lock_timeout')")
                ).scalar_one()
                assert configured == "2s"
                started = time.monotonic()
                with pytest.raises(DBAPIError) as error:
                    waiting.execute(text("SELECT pg_advisory_xact_lock(55937007)"))
                assert getattr(error.value.orig, "sqlstate", None) == "55P03"
                assert time.monotonic() - started < 2.6
        finally:
            waiting.rollback()
            holder.execute(text("SELECT pg_advisory_unlock(55937007)"))
            holder.rollback()
        assert waiting.scalar(text("SELECT 1")) == 1


def test_cancellation_while_settings_return_prevents_actual_statement():
    budget = CaptureBudget(1)
    sent = []

    def execute(query, values):
        sent.append((query, values))
        budget.cancel()

    with capture_budget(budget), pytest.raises(CaptureDeadlineExceeded):
        before_capture_statement(
            SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
            SimpleNamespace(execute=execute),
            "SELECT must_not_dispatch",
            None,
            None,
            False,
        )
    assert len(sent) == 1


def test_setting_failure_never_dispatches_actual_statement():
    def execute(_query, _values):
        raise RuntimeError("synthetic_timeout_setting_failure")

    with capture_budget(CaptureBudget(1)), pytest.raises(RuntimeError):
        before_capture_statement(
            SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
            SimpleNamespace(execute=execute),
            "SELECT must_not_dispatch",
            None,
            None,
            False,
        )
