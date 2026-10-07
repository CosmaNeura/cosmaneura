"""Behavioral contracts for the billing type cleanup; no database or network."""

from types import SimpleNamespace

import pytest
from typeid import TypeID

from app.configuration.clerk import verified_provider_session

from app.models.ai_visit_note import AIVisitNote
from app.models.billing_header_action import BillingHeaderAction
from app.models.billing_header_operation import BillingHeaderPreparedOperation
from app.models.practice import PracticeMember
from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint
from sqlalchemy import select as select_columns
from sqlalchemy.dialects import postgresql
from sqlmodel import SQLModel, col
from sqlmodel.sql.expression import Select


@pytest.mark.parametrize(
    "table_name,column_name,nullable",
    [
        ("billing_header_action", "created_at", False),
        ("billing_header_action", "expires_at", False),
        ("billing_header_action", "finished_at", True),
        ("billing_header_prepared_operation", "completed_at", False),
    ],
)
def test_timestamp_columns_keep_timezone_nullability_and_no_database_defaults(
    table_name, column_name, nullable
):
    column = SQLModel.metadata.tables[table_name].c[column_name]
    assert isinstance(column.type, DateTime)
    assert column.type.timezone is True
    assert column.nullable is nullable
    assert column.default is None and column.server_default is None


def test_inherited_table_names_and_billing_constraints_are_retained():
    action = SQLModel.metadata.tables["billing_header_action"]
    operation = SQLModel.metadata.tables["billing_header_prepared_operation"]
    assert action is getattr(BillingHeaderAction, "__table__")
    assert operation is getattr(BillingHeaderPreparedOperation, "__table__")
    assert {
        str(c.sqltext) for c in action.constraints if isinstance(c, CheckConstraint)
    } == {
        "request_sha256 ~ '^[a-f0-9]{64}$' AND operation_sha256 ~ '^[a-f0-9]{64}$'",
        "purpose IN ('completed-note.accept','header.read','header.generate','header.save')",
        "expires_at > created_at AND expires_at <= created_at + interval '120 seconds'",
    }
    assert {i.name for i in action.indexes} == {"ix_billing_action_request"}
    assert {
        str(c.sqltext) for c in operation.constraints if isinstance(c, CheckConstraint)
    } == {
        "source_revision > 0",
        "request_sha256 ~ '^[a-f0-9]{64}$'",
    }
    assert {
        (c.name, tuple(column.name for column in c.columns))
        for c in operation.constraints
        if isinstance(c, UniqueConstraint)
    } == {
        ("uq_billing_operation_id", ("operation_id",)),
        (
            "uq_billing_source_revision",
            ("appointment_id", "schema_ref", "source_revision"),
        ),
    }


def test_typed_columns_are_the_existing_sqlalchemy_attributes():
    assert col(AIVisitNote.id) is getattr(AIVisitNote, "id")
    assert col(AIVisitNote.note) is getattr(AIVisitNote, "note")
    assert col(PracticeMember.practice_id) is getattr(PracticeMember, "practice_id")
    assert col(BillingHeaderAction.finished_at) is getattr(
        BillingHeaderAction, "finished_at"
    )


def test_multi_column_select_keeps_postgresql_sql_parameters_and_row_shape():
    columns = (
        col(AIVisitNote.id),
        col(AIVisitNote.doctor_id),
        col(AIVisitNote.appointment_id),
        col(AIVisitNote.doctor_note_schema_id),
        col(AIVisitNote.superseded_at),
        col(AIVisitNote.note),
    )
    note_id = TypeID("visit_note")
    original = Select(*columns).where(col(AIVisitNote.id) == note_id).with_for_update()
    typed = (
        select_columns(*columns).where(col(AIVisitNote.id) == note_id).with_for_update()
    )
    before = original.compile(dialect=postgresql.dialect())
    after = typed.compile(dialect=postgresql.dialect())
    assert str(before) == str(after)
    assert before.params == after.params
    assert [item["name"] for item in original.column_descriptions] == [
        item["name"] for item in typed.column_descriptions
    ]


@pytest.mark.parametrize("claim", ["iss", "sub", "sid"])
@pytest.mark.parametrize(
    "invalid", [None, 0, False, [], {}, "", "x" * 513, "bad\nvalue", "bad\x7fvalue"]
)
def test_provider_claim_type_length_and_control_character_rejections_are_retained(
    claim, invalid
):
    payload = {
        "iss": "https://clerk.test.invalid",
        "sub": "user_synthetic",
        "sid": "sess_synthetic",
    }
    payload[claim] = invalid
    with pytest.raises(ValueError, match="^provider_session_invalid$"):
        verified_provider_session(SimpleNamespace(is_signed_in=True, payload=payload))
