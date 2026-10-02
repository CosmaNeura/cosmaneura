"""Dormant email outbox: immutable intent, one irreversible submission lane.

The encrypted invitation is transient and must be cleared at any terminal
outcome. Authorization evidence stays with the parent's retention/legal hold.
This schema does not grant consent, activate delivery, or expose any API.
Adapted and changed from CAB 01ad97536c4f's irreversible submission lane;
Apache-2.0 license retained in docs/licenses/cab-apache-2.0.txt.
"""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepEmailDelivery(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_email"), table=True
):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_email_root",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_generation_id", "visit_prep_id", "owner_practice_member_id"],
            [
                "visit_prep_schedule_generation.id",
                "visit_prep_schedule_generation.visit_prep_id",
                "visit_prep_schedule_generation.owner_practice_member_id",
            ],
            name="fk_visit_prep_email_schedule",
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
            name="fk_visit_prep_email_credential",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "credential_generation",
            name="uq_visit_prep_email_invitation",
        ),
        sa.UniqueConstraint("operation_id", name="uq_visit_prep_email_operation"),
        sa.CheckConstraint("credential_generation > 0", name="credential_shape"),
        sa.CheckConstraint(
            "template_version = 'visit_prep_invitation_v1'", name="template_shape"
        ),
        sa.CheckConstraint(
            "substring(operation_id::text FROM 15 FOR 1) = '4' AND "
            "substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "envelope_format_version = 1 AND "
            "octet_length(authorization_ciphertext) BETWEEN 1 AND 8192 AND "
            "(payload_ciphertext IS NULL OR "
            "octet_length(payload_ciphertext) BETWEEN 1 AND 4096)",
            name="envelope_shape",
        ),
        sa.CheckConstraint(
            "state IN ('queued','submitting','accepted','failed','suppressed','uncertain') "
            "AND ((state = 'queued' AND payload_ciphertext IS NOT NULL "
            "AND submitted_at IS NULL AND lease_until IS NULL AND finalized_at IS NULL) "
            "OR (state = 'submitting' AND payload_ciphertext IS NOT NULL "
            "AND submitted_at IS NOT NULL AND lease_until IS NOT NULL "
            "AND lease_until > submitted_at "
            "AND finalized_at IS NULL) OR "
            "(state IN ('accepted','failed','suppressed','uncertain') "
            "AND payload_ciphertext IS NULL AND lease_until IS NULL "
            "AND finalized_at IS NOT NULL))",
            name="state_shape",
        ),
        sa.CheckConstraint(
            "expires_at > requested_at AND "
            "(submitted_at IS NULL OR submitted_at >= requested_at) AND "
            "(finalized_at IS NULL OR finalized_at >= COALESCE(submitted_at,requested_at))",
            name="time_shape",
        ),
        sa.CheckConstraint(
            "((state IN ('queued','submitting','accepted') AND outcome_code IS NULL) OR "
            "(state = 'uncertain' AND outcome_code = 'submission_uncertain') OR "
            "(state = 'failed' AND outcome_code IN ('transport_unavailable','message_invalid')) OR "
            "(state = 'suppressed' AND outcome_code IN "
            "('scope_unavailable','obsolete_context','consent_withdrawn','expired'))) IS TRUE",
            name="outcome_shape",
        ),
        sa.CheckConstraint(
            "state NOT IN ('accepted','uncertain') OR submitted_at IS NOT NULL",
            name="submission_shape",
        ),
        sa.Index("visit_prep_email_due_idx", "state", "requested_at"),
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
    credential_generation: int
    operation_id: UUID = Field(default_factory=uuid4)
    template_version: str = Field(default="visit_prep_invitation_v1", max_length=64)
    authorization_ciphertext: str = Field(
        repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False)
    )
    payload_ciphertext: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        sa_column=Column(sa.Text(), nullable=True),
    )
    envelope_format_version: int = Field(default=1)
    state: str = Field(default="queued", max_length=16)
    requested_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    expires_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    submitted_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True))
    )
    lease_until: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True))
    )
    finalized_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True))
    )
    outcome_code: str | None = Field(default=None, max_length=32)
