"""Patient-authored follow-up evidence; not model output or an accepted answer.

Each row answers one saved neutral clarification request. Later proposals can
reference this immutable evidence rather than claiming patient words. Retention
and legal holds remain owned by the preparation aggregate.
"""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.visit_prep_clarification import _aggregate_only, _immutable
from sqlmodel import Column, Field


class VisitPrepClarificationTurn(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_clarification_turn"), table=True
):
    """One encrypted patient response to an exact retained clarification prompt."""

    __table_args__ = (
        sa.CheckConstraint("turn_sequence BETWEEN 1 AND 2", name="sequence_range"),
        sa.CheckConstraint(
            "operation_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "envelope_format_version = 1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 16384",
            name="envelope_shape",
        ),
        sa.CheckConstraint("payload_hmac_sha256 ~ '^[0-9a-f]{64}$'", name="hmac_shape"),
        sa.ForeignKeyConstraint(
            ["visit_prep_id"],
            ["visit_prep.id"],
            name="fk_vp_clarification_turn_root",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "visit_prep_id"],
            [
                "visit_prep_clarification_proposal.id",
                "visit_prep_clarification_proposal.visit_prep_id",
            ],
            name="fk_vp_clarification_turn_proposal",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "id", "visit_prep_id", name="uq_vp_clarification_turn_id_root"
        ),
        sa.UniqueConstraint("proposal_id", name="uq_vp_clarification_turn_proposal"),
        sa.UniqueConstraint(
            "visit_prep_id", "operation_id", name="uq_vp_clarification_turn_operation"
        ),
    )

    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    proposal_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_clarification"), nullable=False)
    )
    turn_sequence: int
    operation_id: str = Field(max_length=36)
    envelope_format_version: int = Field(default=1)
    evidence_ciphertext: str = Field(
        repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False)
    )
    payload_hmac_sha256: str = Field(max_length=64, repr=False, exclude=True)
    recorded_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


sa.event.listen(VisitPrepClarificationTurn, "before_update", _immutable)
sa.event.listen(VisitPrepClarificationTurn, "before_delete", _aggregate_only)
