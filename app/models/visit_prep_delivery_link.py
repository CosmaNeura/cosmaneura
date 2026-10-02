"""Verifier-only, short-lived delivery aliases; canonical invitations are unchanged."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepDeliveryLink(BaseModel, TypeIDMixin("visit_prep_link"), table=True):
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_link_root",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["visit_prep_credential_generation.id"],
            name="fk_visit_prep_link_credential",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("token_hash", name="uq_visit_prep_link_token"),
        sa.UniqueConstraint(
            "visit_prep_id", "operation_id", name="uq_visit_prep_link_operation"
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$' AND payload_hmac ~ '^[0-9a-f]{64}$'",
            name="digest_shape",
        ),
        sa.CheckConstraint(
            "substring(operation_id::text FROM 15 FOR 1)='4' AND substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at AND expires_at <= issued_at + interval '24 hours'",
            name="time_shape",
        ),
        sa.CheckConstraint(
            "envelope_format_version=1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 4096",
            name="envelope_shape",
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
    operation_id: UUID
    token_hash: str = Field(max_length=64, repr=False, exclude=True)
    payload_hmac: str = Field(max_length=64, repr=False, exclude=True)
    evidence_ciphertext: str = Field(
        sa_column=Column(sa.Text(), nullable=False), repr=False, exclude=True
    )
    envelope_format_version: int = Field(default=1)
    issued_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    expires_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
