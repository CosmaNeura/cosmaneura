"""Timeout classification must not reconstruct an ORM compilation per read."""

import time

from app.services import companion_records as records
from app.services import scribe_capture_deadline as capture
from app.utils import statement_timeout_scope as settings

from app.models.doctor import Doctor
from sqlalchemy import Select, event, func, literal
from sqlmodel import Session, select

from tests.authority_kernel.scribe_current_timeout_setting_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_current_timeout_setting_test import (
    safe_query,
    setting_calls,
)

engine = _engine


def test_actual_relational_reads_do_not_rebuild_orm_froms(engine, monkeypatch):
    def forbidden_compile(_self):
        raise AssertionError("timeout_classification_recompiled_orm")

    monkeypatch.setattr(Select, "get_final_froms", forbidden_compile)
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            for _ in range(3):
                assert connection.execute(safe_query()).all() == []
            assert calls == [(15000, 2000)]
        session.rollback()


def test_explicit_from_without_relational_columns_keeps_original_setter(
    engine, monkeypatch
):
    with engine.connect() as connection, Session(bind=connection) as session:
        with (
            setting_calls(connection, monkeypatch) as calls,
            capture.capture_budget(capture.CaptureBudget(30)),
            records.transaction_budget(
                session, records.RecordBudget(time.monotonic() + 60)
            ),
        ):
            query = (
                select(literal(1))
                .select_from(Doctor)
                .where(Doctor.clerk_id == "user_invented_timeout_absent")
            )
            for _ in range(3):
                assert connection.execute(query).all() == []
            assert len(calls) == 3
        session.rollback()


def test_each_execution_inspects_once_even_when_compiled_query_is_reused(
    engine, monkeypatch
):
    original = settings.visitors.iterate
    inspections, contexts = [], []

    def inspect(statement, *args, **kwargs):
        inspections.append(statement)
        return original(statement, *args, **kwargs)

    def observe(_connection, _cursor, _statement, _parameters, context, _many):
        contexts.append(context)

    monkeypatch.setattr(settings.visitors, "iterate", inspect)
    with engine.connect() as connection, Session(bind=connection) as session:
        event.listen(connection, "after_cursor_execute", observe)
        try:
            with (
                setting_calls(connection, monkeypatch) as calls,
                capture.capture_budget(capture.CaptureBudget(30)),
                records.transaction_budget(
                    session, records.RecordBudget(time.monotonic() + 60)
                ),
            ):
                query = safe_query()
                for _ in range(3):
                    assert connection.execute(query).all() == []
                assert calls == [(15000, 2000)]
                assert len(inspections) == 3 and len(contexts) == 3
                assert len({id(context) for context in contexts}) == 3
                assert contexts[0].compiled is contexts[1].compiled
                # A function statement must still reset the timeout before its
                # own execution and before the subsequent relational read.
                connection.execute(
                    select(func.set_config("statement_timeout", "0", True))
                ).all()
                before = len(calls)
                assert connection.execute(query).all() == []
                assert len(calls) == before + 1
                assert len(inspections) == 4
        finally:
            event.remove(connection, "after_cursor_execute", observe)
        session.rollback()
