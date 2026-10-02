"""Dormant immutable proposal and decision evidence owned by Core.

Encrypted snapshots retain the exact single-topic context and proposed reply.
Storage integrity is not evidence of model qualification, browser receipt,
patient identity, or consent. The eventual writer must authenticate the actual
patient action and commit the decision and correction in one transaction.
Both streams follow the preparation's clinical retention and legal holds.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepClarificationProposal(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_clarification"), table=True
):
    """Exact bounded proposal prepared for an explicit patient interaction."""

    __table_args__ = (
        sa.CheckConstraint(
            "actor_role IN ('patient', 'caregiver')", name="role_allowed"
        ),
        sa.CheckConstraint("credential_generation > 0", name="credential_positive"),
        sa.CheckConstraint(
            "basis_response_count BETWEEN 1 AND 5 AND basis_event_sequence >= 0",
            name="basis_range",
        ),
        sa.CheckConstraint(
            "source_version ~ '^[0-9a-f]{64}$'", name="source_version_shape"
        ),
        sa.CheckConstraint(
            "(source_response_id IS NULL) <> (source_contribution_id IS NULL)",
            name="one_source",
        ),
        sa.CheckConstraint(
            "operation_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "envelope_format_version = 1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 65536",
            name="envelope_shape",
        ),
        sa.CheckConstraint("payload_hmac_sha256 ~ '^[0-9a-f]{64}$'", name="hmac_shape"),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_vp_clarification_root",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_set_version_id", "question_id"],
            ["visit_prep_question.question_set_version_id", "visit_prep_question.id"],
            name="fk_vp_clarification_question",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "credential_generation"],
            [
                "visit_prep_credential_generation.visit_prep_id",
                "visit_prep_credential_generation.generation",
            ],
            name="fk_vp_clarification_credential",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["source_response_id"],
            ["visit_prep_question_response.id"],
            name="fk_vp_clarification_response",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["source_contribution_id"],
            ["visit_prep_contribution_event.id"],
            name="fk_vp_clarification_source",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("id", "visit_prep_id", name="uq_vp_clarification_id_root"),
        sa.UniqueConstraint(
            "visit_prep_id", "operation_id", name="uq_vp_clarification_operation"
        ),
        sa.Index("vp_clarification_root_idx", "visit_prep_id", "recorded_at"),
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
    question_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_question"), nullable=False)
    )
    credential_generation: int
    actor_role: str = Field(max_length=16)
    basis_response_count: int
    basis_event_sequence: int
    source_version: str = Field(max_length=64)
    source_response_id: TypeIDType | None = Field(
        default=None, sa_column=Column(TypeIDType("visit_prep_response"), nullable=True)
    )
    source_contribution_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("visit_prep_contribution"), nullable=True),
    )
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


class VisitPrepClarificationDecision(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_clarification_decision"),
    table=True,
):
    """One explicit final decision, with a correction when text is accepted.

    Choice supplementation requires its own qualified source write; this expand
    rung does not permit disguising explanatory words as a coded choice value.
    """

    __table_args__ = (
        sa.CheckConstraint(
            "action IN ('confirm', 'edit', 'keep_original', 'dismiss')",
            name="action_allowed",
        ),
        sa.CheckConstraint(
            "(action IN ('confirm', 'edit') AND correction_id IS NOT NULL) OR (action IN ('keep_original', 'dismiss') AND correction_id IS NULL)",
            name="correction_shape",
        ),
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
            ["proposal_id", "visit_prep_id"],
            [
                "visit_prep_clarification_proposal.id",
                "visit_prep_clarification_proposal.visit_prep_id",
            ],
            name="fk_vp_clarification_decision_proposal",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["correction_id"],
            ["visit_prep_contribution_event.id"],
            name="fk_vp_clarification_decision_correction",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "proposal_id", name="uq_vp_clarification_decision_proposal"
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "operation_id",
            name="uq_vp_clarification_decision_operation",
        ),
    )

    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    proposal_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_clarification"), nullable=False)
    )
    action: str = Field(max_length=16)
    correction_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("visit_prep_contribution"), nullable=True),
    )
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


def _immutable(*_: object) -> None:
    raise RuntimeError("Visit Prep clarification evidence is append-only.")


def _aggregate_only(*_: object) -> None:
    raise RuntimeError("Visit Prep clarification evidence requires aggregate cleanup.")


for _model in (VisitPrepClarificationProposal, VisitPrepClarificationDecision):
    sa.event.listen(_model, "before_update", _immutable)
    sa.event.listen(_model, "before_delete", _aggregate_only)
