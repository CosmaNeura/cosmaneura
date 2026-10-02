"""Separate SMS permission evidence; not an email grant or sending capability.

Retain evidence until its consent record class and legal-hold cleanup qualify.
No contact value, invitation bearer, message body or patient answer is copied.
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepSMSConsentEvent(
    BaseModel, TypeIDMixin("visit_prep_sms_consent"), table=True
):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_sms_consent_root",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["visit_prep_credential_generation.id"],
            name="fk_visit_prep_sms_consent_credential",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "visit_prep_id", "event_sequence", name="uq_visit_prep_sms_consent_sequence"
        ),
        sa.UniqueConstraint(
            "visit_prep_id", "operation_id", name="uq_visit_prep_sms_consent_operation"
        ),
        sa.CheckConstraint(
            "event_sequence > 0 AND event_kind IN ('granted','withdrawn') AND actor_role IN ('patient','caregiver')",
            name="choice_shape",
        ),
        sa.CheckConstraint(
            "recipient_digest ~ '^[0-9a-f]{64}$' AND disclosure_fingerprint ~ '^[0-9a-f]{64}$' AND payload_hmac_sha256 ~ '^[0-9a-f]{64}$'",
            name="digest_shape",
        ),
        sa.CheckConstraint(
            "substring(operation_id::text FROM 15 FOR 1) = '4' AND substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "envelope_format_version = 1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 8192",
            name="evidence_shape",
        ),
        sa.CheckConstraint(
            "effective_at <= recorded_at AND ((event_kind = 'granted' AND expires_at > recorded_at AND expires_at IS NOT NULL) OR (event_kind = 'withdrawn' AND expires_at IS NULL))",
            name="time_shape",
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
    event_sequence: int
    event_kind: str = Field(max_length=16)
    actor_role: str = Field(max_length=16)
    operation_id: UUID
    recipient_digest: str = Field(max_length=64, repr=False, exclude=True)
    disclosure_fingerprint: str = Field(max_length=64)
    payload_hmac_sha256: str = Field(max_length=64, repr=False, exclude=True)
    evidence_ciphertext: str = Field(
        repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False)
    )
    envelope_format_version: int = Field(default=1)
    effective_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    recorded_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True))
    )


def _deny_mutation(*_):
    raise RuntimeError("Visit Prep SMS consent evidence is append-only.")


sa.event.listen(VisitPrepSMSConsentEvent, "before_update", _deny_mutation)
sa.event.listen(VisitPrepSMSConsentEvent, "before_delete", _deny_mutation)
