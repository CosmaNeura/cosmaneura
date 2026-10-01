"""Dormant append-only patient corrections and exact-contribution review evidence.

Original question responses remain immutable. Each event is attributed to the
confirmed recipient role and current invitation generation. Its basis captures
both the original-response count and the preceding event sequence; services must
compare both before treating an acknowledgment as current. This is patient
report, not clinician verification or authority for downstream actions.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepContributionEvent(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_contribution"),
    table=True,
):
    """One attributable correction or review of a specific contribution basis."""

    __table_args__ = (
        sa.CheckConstraint(
            "event_kind IN ('correction', 'review')", name="event_kind_allowed"
        ),
        sa.CheckConstraint(
            "actor_role IN ('patient', 'caregiver')", name="actor_role_allowed"
        ),
        sa.CheckConstraint(
            "credential_generation > 0", name="credential_generation_positive"
        ),
        sa.CheckConstraint(
            "basis_event_sequence >= 0 AND event_sequence = basis_event_sequence + 1",
            name="event_sequence_basis",
        ),
        sa.CheckConstraint(
            "basis_response_count BETWEEN 1 AND 5", name="basis_response_count_range"
        ),
        sa.CheckConstraint(
            "(event_kind = 'review' AND response_id IS NULL "
            "AND response_kind IS NULL AND answer_ciphertext IS NULL "
            "AND answer_format_version IS NULL) OR "
            "(event_kind = 'correction' AND response_id IS NOT NULL "
            "AND response_kind IS NOT NULL AND "
            "((response_kind = 'answered' AND answer_ciphertext IS NOT NULL "
            "AND answer_format_version IS NOT DISTINCT FROM 1 "
            "AND octet_length(answer_ciphertext) BETWEEN 1 AND 32768) OR "
            "(response_kind = 'skipped' AND answer_ciphertext IS NULL "
            "AND answer_format_version IS NULL)))",
            name="event_shape",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="idempotency_key_shape",
        ),
        sa.CheckConstraint(
            "payload_hmac_sha256 ~ '^[0-9a-f]{64}$'", name="payload_hmac_shape"
        ),
        sa.CheckConstraint(
            "recorded_at >= created_at", name="recorded_at_not_before_creation"
        ),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_contribution_root",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["response_id"],
            ["visit_prep_question_response.id"],
            name="fk_visit_prep_contribution_response",
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
            name="fk_visit_prep_contribution_credential",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "event_sequence",
            name="uq_visit_prep_contribution_sequence",
        ),
        sa.UniqueConstraint(
            "visit_prep_id", "idempotency_key", name="uq_visit_prep_contribution_key"
        ),
        sa.Index(
            "visit_prep_contribution_response_idx", "response_id", "event_sequence"
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
    credential_generation: int
    actor_role: str = Field(max_length=16)
    event_kind: str = Field(max_length=16)
    event_sequence: int
    basis_event_sequence: int
    basis_response_count: int
    response_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("visit_prep_response"), nullable=True),
    )
    response_kind: str | None = Field(default=None, max_length=16)
    answer_ciphertext: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        sa_column=Column(sa.Text(), nullable=True),
    )
    answer_format_version: int | None = Field(default=None)
    idempotency_key: str = Field(max_length=36)
    payload_hmac_sha256: str = Field(max_length=64, repr=False, exclude=True)
    recorded_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
        sa_column_kwargs={"server_default": sa.func.now()},
        nullable=False,
    )


def _prevent_contribution_update(*_: object) -> None:
    raise RuntimeError("Visit Prep contribution events are append-only.")


def _prevent_contribution_delete(*_: object) -> None:
    raise RuntimeError(
        "Visit Prep contribution events delete only with aggregate cleanup."
    )


sa.event.listen(
    VisitPrepContributionEvent, "before_update", _prevent_contribution_update
)
sa.event.listen(
    VisitPrepContributionEvent, "before_delete", _prevent_contribution_delete
)
