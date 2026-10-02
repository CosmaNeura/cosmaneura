"""Append-only physician SMS policy choices, not patient consent or sender approval."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepSMSApprovalEvent(
    BaseModel, TypeIDMixin("visit_prep_sms_approval"), table=True
):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["owner_practice_member_id", "practice_id", "owner_doctor_id"],
            [
                "practice_member.id",
                "practice_member.practice_id",
                "practice_member.doctor_id",
            ],
            name="fk_visit_prep_sms_approval_owner",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "owner_practice_member_id",
            "event_sequence",
            name="uq_visit_prep_sms_approval_sequence",
        ),
        sa.UniqueConstraint(
            "owner_practice_member_id",
            "operation_id",
            name="uq_visit_prep_sms_approval_operation",
        ),
        sa.CheckConstraint(
            "event_sequence BETWEEN 1 AND 2147483647", name="sequence_shape"
        ),
        sa.CheckConstraint(
            "event_kind IN ('approved','withdrawn') AND template_version='visit_prep_sms_v1'",
            name="policy_shape",
        ),
        sa.CheckConstraint(
            "sender_digest ~ '^[0-9a-f]{64}$' AND subject_digest ~ '^[0-9a-f]{64}$' "
            "AND policy_fingerprint ~ '^[0-9a-f]{64}$' AND payload_hmac ~ '^[0-9a-f]{64}$'",
            name="digest_shape",
        ),
        sa.CheckConstraint(
            "substring(operation_id::text FROM 15 FOR 1) = '4' AND substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "envelope_format_version=1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 4096",
            name="envelope_shape",
        ),
        sa.CheckConstraint(
            "((event_kind='approved' AND expires_at IS NOT NULL AND expires_at > recorded_at) OR (event_kind='withdrawn' AND expires_at IS NULL)) IS TRUE",
            name="time_shape",
        ),
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    owner_doctor_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("doctor"), nullable=False)
    )
    event_sequence: int
    event_kind: str = Field(max_length=16)
    template_version: str = Field(default="visit_prep_sms_v1", max_length=32)
    sender_digest: str = Field(max_length=64, repr=False, exclude=True)
    subject_digest: str = Field(max_length=64, repr=False, exclude=True)
    policy_fingerprint: str = Field(max_length=64)
    operation_id: UUID
    payload_hmac: str = Field(max_length=64, repr=False, exclude=True)
    evidence_ciphertext: str = Field(
        sa_column=Column(sa.Text(), nullable=False), repr=False, exclude=True
    )
    envelope_format_version: int = Field(default=1)
    recorded_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True))
    )
