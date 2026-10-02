"""Immutable booking-to-preparation receipt, not a patient or appointment store."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepBookingReceipt(BaseModel, TypeIDMixin("visit_prep_booking"), table=True):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_booking_root",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "connection_id", "booking_digest", name="uq_visit_prep_booking_source"
        ),
        sa.UniqueConstraint("visit_prep_id", name="uq_visit_prep_booking_preparation"),
        sa.CheckConstraint(
            "source = 'acuity' AND booking_digest ~ '^[0-9a-f]{64}$' AND snapshot_hmac ~ '^[0-9a-f]{64}$'",
            name="source_shape",
        ),
        sa.CheckConstraint("recorded_at IS NOT NULL", name="recorded_shape"),
    )
    connection_id: UUID
    source: str = Field(default="acuity", max_length=32)
    booking_digest: str = Field(max_length=64, repr=False)
    snapshot_hmac: str = Field(max_length=64, repr=False)
    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    question_set_version_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_question_set_version"), nullable=False)
    )
    recorded_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )


def _deny_mutation(*_):
    raise RuntimeError(
        "Visit Prep booking receipts are immutable; retain source evidence."
    )


sa.event.listen(VisitPrepBookingReceipt, "before_update", _deny_mutation)
sa.event.listen(VisitPrepBookingReceipt, "before_delete", _deny_mutation)
