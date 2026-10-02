"""Unmatched cancellation observations; no patient, contact or invitation copy.

Retain this append-only source fence until its evidence cleanup is qualified.
A later fresh active source can create a preparation; a stale response cannot.
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepBookingCancellation(
    BaseModel, TypeIDMixin("visit_prep_booking_cancellation"), table=True
):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["owner_practice_member_id", "practice_id", "owner_doctor_id"],
            [
                "practice_member.id",
                "practice_member.practice_id",
                "practice_member.doctor_id",
            ],
            name="fk_visit_prep_booking_cancellation_owner",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "connection_id",
            "booking_digest",
            "sequence",
            name="uq_visit_prep_booking_cancellation_sequence",
        ),
        sa.CheckConstraint("sequence > 0", name="sequence_shape"),
        sa.CheckConstraint(
            "source = 'acuity' AND booking_digest ~ '^[0-9a-f]{64}$' AND "
            "source_snapshot_hmac ~ '^[0-9a-f]{64}$' AND "
            "connection_id <> '00000000-0000-0000-0000-000000000000'::uuid",
            name="source_shape",
        ),
        sa.CheckConstraint(
            "observed_at IS NOT NULL AND recorded_at IS NOT NULL AND observed_at <= recorded_at",
            name="time_shape",
        ),
    )
    connection_id: UUID
    source: str = Field(default="acuity", max_length=32)
    booking_digest: str = Field(max_length=64, repr=False)
    source_snapshot_hmac: str = Field(max_length=64, repr=False)
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    owner_doctor_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("doctor"), nullable=False)
    )
    sequence: int
    observed_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    recorded_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )


def _deny_mutation(*_):
    raise RuntimeError("Visit Prep unmatched cancellation evidence is immutable.")


sa.event.listen(VisitPrepBookingCancellation, "before_update", _deny_mutation)
sa.event.listen(VisitPrepBookingCancellation, "before_delete", _deny_mutation)
