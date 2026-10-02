"""Append-only booking lifecycle evidence; no contact or invitation bearer copy."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepBookingEvent(
    BaseModel, TypeIDMixin("visit_prep_booking_event"), table=True
):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["booking_receipt_id"],
            ["visit_prep_booking_receipt.id"],
            name="fk_visit_prep_booking_event_receipt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_booking_event_root",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_generation_id"],
            ["visit_prep_schedule_generation.id"],
            name="fk_visit_prep_booking_event_schedule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_generation_id"],
            ["visit_prep_credential_generation.id"],
            name="fk_visit_prep_booking_event_credential",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "booking_receipt_id",
            "sequence",
            name="uq_visit_prep_booking_event_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="sequence_shape"),
        sa.CheckConstraint(
            "action IN ('rescheduled', 'cancelled', 'recipient_changed')",
            name="action_shape",
        ),
        sa.CheckConstraint(
            "prior_snapshot_hmac ~ '^[0-9a-f]{64}$' AND "
            "source_snapshot_hmac ~ '^[0-9a-f]{64}$'",
            name="snapshot_shape",
        ),
        sa.CheckConstraint(
            "observed_at IS NOT NULL AND recorded_at IS NOT NULL AND "
            "observed_at <= recorded_at",
            name="time_shape",
        ),
    )
    booking_receipt_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_booking"), nullable=False)
    )
    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    question_set_version_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_question_set_version"), nullable=False)
    )
    schedule_generation_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_schedule"), nullable=False)
    )
    credential_generation_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_credential"), nullable=False)
    )
    sequence: int
    action: str = Field(max_length=32)
    prior_snapshot_hmac: str = Field(max_length=64, repr=False)
    source_snapshot_hmac: str = Field(max_length=64, repr=False)
    observed_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    recorded_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )


def _deny_mutation(*_):
    raise RuntimeError("Visit Prep booking lifecycle evidence is immutable.")


sa.event.listen(VisitPrepBookingEvent, "before_update", _deny_mutation)
sa.event.listen(VisitPrepBookingEvent, "before_delete", _deny_mutation)
