"""Immutable evidence for binding one NULL legacy grant to its current practice."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class PilotAccessPracticeBinding(
    BaseModel, TimestampsMixin, TypeIDMixin("pilot_practice_binding"), table=True
):
    __table_args__ = (
        sa.CheckConstraint(
            "predecessor_grant_id <> successor_grant_id",
            name="ck_pilot_practice_binding_distinct_grants",
        ),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="ck_pilot_practice_binding_environment",
        ),
        sa.CheckConstraint(
            "plan_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pilot_practice_binding_plan_digest",
        ),
    )

    predecessor_grant_id: TypeIDType = Field(
        sa_type=TypeIDType("pilot_access_grant"),  # type: ignore
        foreign_key="pilot_access_grandfather_grant.id",
        unique=True,
    )
    successor_grant_id: TypeIDType = Field(
        sa_type=TypeIDType("pilot_access_grant"),  # type: ignore
        foreign_key="pilot_access_grandfather_grant.id",
        unique=True,
    )
    receipt_id: TypeIDType = Field(
        sa_type=TypeIDType("practice_access_audit"),  # type: ignore
        foreign_key="practice_access_audit.id",
        unique=True,
    )
    environment: str = Field(max_length=16)
    plan_sha256: str = Field(max_length=64, unique=True)
    payload_json: str = Field(sa_column=Column(sa.Text(), nullable=False))
    applied_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
