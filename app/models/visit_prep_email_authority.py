"""Dormant append-only email consent and physician standing-approval evidence.

These are distinct gates. A contact, BAA, booking connection or physician
approval does not imply patient consent. Commands must validate encrypted
provenance and current authority; workers must reacquire both latest streams.
No evidence row itself grants a capability or connects a sending workflow.

Deletion is deliberately unavailable in this expand until the consent-evidence
record class and its cleanup/hold rules are qualified. Existing unrelated Visit
Prep retention is not changed. Never silently cascade away these authorizations.
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepEmailApprovalEvent(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_email_approval"), table=True
):
    """A physician's versioned approval/withdrawal of the fixed invitation policy."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["owner_practice_member_id", "practice_id", "owner_doctor_id"],
            [
                "practice_member.id",
                "practice_member.practice_id",
                "practice_member.doctor_id",
            ],
            name="fk_visit_prep_email_approval_owner",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "owner_practice_member_id",
            "event_sequence",
            name="uq_visit_prep_email_approval_sequence",
        ),
        sa.UniqueConstraint(
            "owner_practice_member_id",
            "operation_id",
            name="uq_visit_prep_email_approval_operation",
        ),
        sa.CheckConstraint("event_sequence > 0", name="sequence_shape"),
        sa.CheckConstraint(
            "event_kind IN ('approved','withdrawn') AND template_version = 'visit_prep_invitation_v1'",
            name="policy_shape",
        ),
        sa.CheckConstraint(
            "policy_fingerprint ~ '^[0-9a-f]{64}$' AND payload_hmac_sha256 ~ '^[0-9a-f]{64}$'",
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
            "((event_kind = 'approved' AND expires_at IS NOT NULL AND expires_at > recorded_at) OR (event_kind = 'withdrawn' AND expires_at IS NULL)) AND recorded_at >= created_at",
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
    template_version: str = Field(default="visit_prep_invitation_v1", max_length=64)
    policy_fingerprint: str = Field(max_length=64)
    operation_id: UUID
    payload_hmac_sha256: str = Field(max_length=64, repr=False, exclude=True)
    evidence_ciphertext: str = Field(
        repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False)
    )
    envelope_format_version: int = Field(default=1)
    recorded_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True))
    )


class VisitPrepEmailConsentEvent(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_email_consent"), table=True
):
    """Recipient communication permission, scoped to one preparation and mailbox.

    Granted/withdrawn are assertions backed by separately encrypted provenance,
    not identity verification or consent to broader clinical/pastoral activity.
    A caregiver must have attributable authority in that evidence. A trusted
    source is accepted only by a future verified source adapter, never a browser.
    """

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_email_consent_root",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "event_sequence",
            name="uq_visit_prep_email_consent_sequence",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "operation_id",
            name="uq_visit_prep_email_consent_operation",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "source_event_id",
            name="uq_visit_prep_email_consent_source",
        ),
        sa.CheckConstraint("event_sequence > 0", name="sequence_shape"),
        sa.CheckConstraint(
            "event_kind IN ('granted','withdrawn') AND actor_role IN ('patient','caregiver','trusted_source') AND purpose = 'visit_prep_email_invitation'",
            name="consent_shape",
        ),
        sa.CheckConstraint(
            "recipient_digest ~ '^[0-9a-f]{64}$' AND payload_hmac_sha256 ~ '^[0-9a-f]{64}$'",
            name="digest_shape",
        ),
        sa.CheckConstraint(
            "substring(operation_id::text FROM 15 FOR 1) = '4' AND substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b') AND substring(source_event_id::text FROM 15 FOR 1) = '4' AND substring(source_event_id::text FROM 20 FOR 1) IN ('8','9','a','b')",
            name="operation_shape",
        ),
        sa.CheckConstraint(
            "envelope_format_version = 1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 8192",
            name="evidence_shape",
        ),
        sa.CheckConstraint(
            "effective_at <= recorded_at AND recorded_at >= created_at AND ((event_kind = 'granted' AND expires_at IS NOT NULL AND expires_at > recorded_at) OR (event_kind = 'withdrawn' AND expires_at IS NULL))",
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
    event_sequence: int
    event_kind: str = Field(max_length=16)
    actor_role: str = Field(max_length=16)
    purpose: str = Field(default="visit_prep_email_invitation", max_length=64)
    recipient_digest: str = Field(max_length=64, repr=False, exclude=True)
    operation_id: UUID
    source_event_id: UUID
    payload_hmac_sha256: str = Field(max_length=64, repr=False, exclude=True)
    evidence_ciphertext: str = Field(
        repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False)
    )
    envelope_format_version: int = Field(default=1)
    recorded_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    effective_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True))
    )


def _deny_authority_update(*_: object) -> None:
    raise RuntimeError("Visit Prep email authority evidence is append-only.")


def _deny_authority_delete(*_: object) -> None:
    raise RuntimeError(
        "Visit Prep email authority evidence requires a qualified retention cleanup."
    )


for _model in (VisitPrepEmailApprovalEvent, VisitPrepEmailConsentEvent):
    sa.event.listen(_model, "before_update", _deny_authority_update)
    sa.event.listen(_model, "before_delete", _deny_authority_delete)
