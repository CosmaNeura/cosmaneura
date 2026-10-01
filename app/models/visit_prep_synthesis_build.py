"""Dormant Core-owned synthesis queue; no patient text or model usage store.

One mutable build per preparation/profile holds a source fence and short lease.
The encrypted requester envelope preserves the original sign-in binding without
persisting a raw identity. Retention/cleanup belongs to the Visit Prep parent.
"""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepSynthesisBuild(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_build"), table=True
):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_build_root",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "generator_version",
            "prompt_version",
            "model_profile",
            name="uq_visit_prep_build_profile",
        ),
        sa.CheckConstraint(
            "epoch > 0 AND attempts BETWEEN 0 AND 3", name="fence_shape"
        ),
        sa.CheckConstraint("source_version ~ '^[0-9a-f]{64}$'", name="source_shape"),
        sa.CheckConstraint(
            "generator_version ~ '^[a-z][a-z0-9_]{0,63}$' AND "
            "prompt_version ~ '^[a-z][a-z0-9_]{0,63}$' AND "
            "model_profile ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="profile_shape",
        ),
        sa.CheckConstraint(
            "requester_format_version = 1 AND "
            "octet_length(requester_ciphertext) BETWEEN 1 AND 4096",
            name="requester_shape",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'ready', 'failed') AND "
            "((state = 'running' AND lease_until IS NOT NULL AND attempts > 0) OR "
            "(state <> 'running' AND lease_until IS NULL)) AND "
            "(state <> 'pending' OR attempts < 3)",
            name="state_shape",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN "
            "('scope_unavailable', 'generation_failed', 'usage_unavailable', "
            "'worker_attempts_exhausted')",
            name="failure_shape",
        ),
        sa.CheckConstraint(
            "(state <> 'failed' OR failure_code IS NOT NULL) AND "
            "(state NOT IN ('running', 'ready') OR failure_code IS NULL)",
            name="failure_state",
        ),
        sa.Index("visit_prep_build_due_idx", "state", "retry_at", "requested_at"),
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
    generator_version: str = Field(max_length=64)
    prompt_version: str = Field(max_length=64)
    model_profile: str = Field(max_length=128)
    source_version: str = Field(max_length=64)
    requester_ciphertext: str = Field(
        repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False)
    )
    requester_format_version: int = Field(default=1)
    epoch: int = Field(default=1)
    attempts: int = Field(default=0)
    state: str = Field(default="pending", max_length=16)
    requested_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    retry_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    lease_until: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    failure_code: str | None = Field(default=None, max_length=32)
