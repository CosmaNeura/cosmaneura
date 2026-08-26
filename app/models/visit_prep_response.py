"""Dormant, immutable answer storage for Visit Prep.

This expand layer deliberately exposes no patient-facing behavior.  Every
accepted answer or skip is one append-only row tied to the exact Visit Prep,
published question version, question identity, and credential generation that
accepted it.  Later services must lock the root ``VisitPrep`` row and preserve
the same database-enforced ordering and idempotency rules.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepQuestionResponse(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_response"),
    table=True,
):
    """One immutable answer or explicit skip for an exact question."""

    __table_args__ = (
        sa.CheckConstraint(
            "submission_sequence BETWEEN 1 AND 5",
            name="submission_sequence_range",
        ),
        sa.CheckConstraint(
            "credential_generation > 0",
            name="credential_generation_positive",
        ),
        sa.CheckConstraint(
            "response_kind IN ('answered', 'skipped')",
            name="response_kind_allowed",
        ),
        sa.CheckConstraint(
            "(response_kind = 'answered' "
            "AND answer_ciphertext IS NOT NULL "
            "AND answer_format_version = 1 "
            "AND octet_length(answer_ciphertext) BETWEEN 1 AND 32768) OR "
            "(response_kind = 'skipped' "
            "AND answer_ciphertext IS NULL "
            "AND answer_format_version IS NULL)",
            name="answer_shape",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="idempotency_key_shape",
        ),
        sa.CheckConstraint(
            "payload_hmac_sha256 ~ '^[0-9a-f]{64}$'",
            name="payload_hmac_sha256_shape",
        ),
        sa.CheckConstraint(
            "recorded_at >= created_at",
            name="recorded_at_not_before_creation",
        ),
        sa.ForeignKeyConstraint(
            [
                "visit_prep_id",
                "owner_practice_member_id",
                "question_set_version_id",
            ],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_response_root_lineage",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_set_version_id", "question_id"],
            [
                "visit_prep_question.question_set_version_id",
                "visit_prep_question.id",
            ],
            name="fk_visit_prep_response_question_lineage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "credential_generation"],
            [
                "visit_prep_credential_generation.visit_prep_id",
                "visit_prep_credential_generation.generation",
            ],
            name="fk_visit_prep_response_credential_generation",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "question_id",
            name="uq_visit_prep_response_question",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "submission_sequence",
            name="uq_visit_prep_response_sequence",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "idempotency_key",
            name="uq_visit_prep_response_idempotency",
        ),
        sa.Index(
            "visit_prep_response_owner_prep_idx",
            "owner_practice_member_id",
            "visit_prep_id",
        ),
        sa.Index(
            "visit_prep_response_question_lineage_idx",
            "question_set_version_id",
            "question_id",
        ),
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
    submission_sequence: int
    response_kind: str = Field(max_length=16)
    # Keep the encrypted envelope explicit.  A transparent decrypting type
    # would surface corrupt or retired-key material while ORM rows load.
    answer_ciphertext: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        sa_column=Column(sa.Text(), nullable=True),
    )
    answer_format_version: int | None = Field(
        default=None,
        sa_column=Column(sa.Integer(), nullable=True),
    )
    idempotency_key: str = Field(max_length=36)
    # The request contains guessable answers, so this must be a
    # context-separated keyed HMAC rather than a plain digest.
    payload_hmac_sha256: str = Field(
        max_length=64,
        repr=False,
        exclude=True,
    )
    recorded_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
        sa_column_kwargs={"server_default": sa.func.now()},
        nullable=False,
    )


def _prevent_visit_prep_response_update(*_: object) -> None:
    raise RuntimeError("Visit Prep responses are append-only.")


def _prevent_visit_prep_response_delete(*_: object) -> None:
    raise RuntimeError("Visit Prep responses delete only with aggregate cleanup.")


sa.event.listen(
    VisitPrepQuestionResponse,
    "before_update",
    _prevent_visit_prep_response_update,
)
sa.event.listen(
    VisitPrepQuestionResponse,
    "before_delete",
    _prevent_visit_prep_response_delete,
)
