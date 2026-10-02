"""Separate SMS outbox; immutable intent and one irreversible submission lane.

Contact/link payload is transient; retained evidence contains normalized scope and digests.
No schema field grants consent, approval, a sender identity or live activation.
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepSMSDelivery(BaseModel, TypeIDMixin("visit_prep_sms"), table=True):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_sms_root",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["visit_prep_credential_generation.id"],
            name="fk_visit_prep_sms_credential",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["consent_event_id"],
            ["visit_prep_sms_consent_event.id"],
            name="fk_visit_prep_sms_consent",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "visit_prep_id", "credential_id", "step", name="uq_visit_prep_sms_step"
        ),
        sa.UniqueConstraint("operation_id", name="uq_visit_prep_sms_operation"),
        sa.Index("ix_visit_prep_sms_due", "state", "requested_at"),
        sa.Index(
            "ix_visit_prep_sms_channel",
            "sender_digest",
            "recipient_digest",
            "requested_at",
        ),
        sa.CheckConstraint(
            "step IN ('booking_sms','unopened_48h_sms','unfinished_12h_sms') "
            "AND template_version = 'visit_prep_sms_v1'",
            name="step_shape",
        ),
        sa.CheckConstraint(
            "substring(operation_id::text FROM 15 FOR 1) = '4' AND "
            "substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "sender_digest ~ '^[0-9a-f]{64}$' AND recipient_digest ~ '^[0-9a-f]{64}$' "
            "AND request_hmac ~ '^[0-9a-f]{64}$' AND payload_hmac ~ '^[0-9a-f]{64}$' "
            "AND evidence_hmac ~ '^[0-9a-f]{64}$'",
            name="digest_shape",
        ),
        sa.CheckConstraint(
            "octet_length(evidence_ciphertext) BETWEEN 1 AND 8192 AND "
            "(payload_ciphertext IS NULL OR octet_length(payload_ciphertext) BETWEEN 1 AND 4096)",
            name="envelope_shape",
        ),
        sa.CheckConstraint(
            "state IN ('queued','submitting','accepted','failed','suppressed','uncertain') AND "
            "((state='queued' AND payload_ciphertext IS NOT NULL AND submitted_at IS NULL "
            "AND lease_until IS NULL AND finalized_at IS NULL) OR "
            "(state='submitting' AND payload_ciphertext IS NOT NULL AND submitted_at IS NOT NULL "
            "AND lease_until IS NOT NULL AND lease_until > submitted_at AND finalized_at IS NULL) OR "
            "(state IN ('accepted','failed','suppressed','uncertain') AND payload_ciphertext IS NULL "
            "AND lease_until IS NULL AND finalized_at IS NOT NULL))",
            name="state_shape",
        ),
        sa.CheckConstraint(
            "expires_at > requested_at AND (submitted_at IS NULL OR submitted_at >= requested_at) "
            "AND (finalized_at IS NULL OR finalized_at >= COALESCE(submitted_at,requested_at)) "
            "AND (state NOT IN ('accepted','uncertain') OR submitted_at IS NOT NULL)",
            name="time_shape",
        ),
        sa.CheckConstraint(
            "((state IN ('queued','submitting','accepted') AND outcome_code IS NULL) OR "
            "(state='uncertain' AND outcome_code='submission_uncertain') OR "
            "(state='failed' AND outcome_code='transport_unavailable') OR "
            "(state='suppressed' AND outcome_code IN ('scope_unavailable','obsolete_context','expired'))) IS TRUE",
            name="outcome_shape",
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
    credential_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_credential"), nullable=False)
    )
    consent_event_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_sms_consent"), nullable=False)
    )
    operation_id: UUID
    step: str = Field(max_length=32)
    template_version: str = Field(default="visit_prep_sms_v1", max_length=32)
    sender_digest: str = Field(max_length=64, repr=False, exclude=True)
    recipient_digest: str = Field(max_length=64, repr=False, exclude=True)
    request_hmac: str = Field(max_length=64, repr=False, exclude=True)
    payload_hmac: str = Field(max_length=64, repr=False, exclude=True)
    evidence_hmac: str = Field(max_length=64, repr=False, exclude=True)
    evidence_ciphertext: str = Field(
        sa_column=Column(sa.Text(), nullable=False), repr=False, exclude=True
    )
    payload_ciphertext: str | None = Field(
        default=None, sa_column=Column(sa.Text()), repr=False, exclude=True
    )
    state: str = Field(default="queued", max_length=16)
    requested_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    expires_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
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
