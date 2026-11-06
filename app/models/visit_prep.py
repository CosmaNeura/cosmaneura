from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

_UUID_RE = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"


# fmt: off
class VisitPrep(BaseModel, TimestampsMixin, TypeIDMixin("visit_prep"), table=True):
    __table_args__ = (
        sa.CheckConstraint("lifecycle_state IN ('invited', 'confirmed', 'in_progress', 'completed')", name="vp_lifecycle_state_allowed"),
        sa.CheckConstraint(f"creation_idempotency_key ~ {_UUID_RE}", name="vp_creation_idempotency_key_shape"),
        sa.CheckConstraint("creation_payload_hmac_sha256 ~ '^[0-9a-f]{64}$'", name="vp_creation_payload_hmac_shape"),
        sa.CheckConstraint("octet_length(recipient_snapshot_ciphertext) BETWEEN 1 AND 16384", name="vp_recipient_ciphertext_size"),
        sa.CheckConstraint("has_email_contact IS TRUE OR has_sms_contact IS TRUE", name="vp_recipient_has_contact"),
        sa.CheckConstraint("char_length(btrim(physician_display_name)) BETWEEN 1 AND 160", name="vp_physician_name_normalized"),
        sa.CheckConstraint("char_length(btrim(practice_display_name)) BETWEEN 1 AND 200", name="vp_practice_name_normalized"),
        sa.CheckConstraint("credential_generation > 0", name="vp_credential_generation_positive"),
        sa.CheckConstraint("schedule_generation > 0", name="vp_schedule_generation_positive"),
        sa.ForeignKeyConstraint(["owner_practice_member_id"], ["practice_member.id"], name="fk_vp_owner_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["practice_id"], ["practice.id"], name="fk_vp_practice", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_doctor_id"], ["doctor.id"], name="fk_vp_owner_doctor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["question_set_version_id", "owner_practice_member_id"], ["visit_prep_question_set_version.id", "visit_prep_question_set_version.owner_practice_member_id"], name="fk_vp_question_version_owner", ondelete="RESTRICT"),
        sa.UniqueConstraint("owner_practice_member_id", "creation_idempotency_key", name="uq_vp_owner_idempotency"),
        sa.UniqueConstraint("token_hash", name="uq_vp_token_hash"),
        sa.Index("visit_prep_owner_idx", "owner_practice_member_id", "id"),
    )
    practice_id: TypeIDType = Field(sa_column=Column(TypeIDType("practice"), nullable=False))
    owner_practice_member_id: TypeIDType = Field(sa_column=Column(TypeIDType("practice_member"), nullable=False))
    owner_doctor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    question_set_version_id: TypeIDType = Field(sa_column=Column(TypeIDType("visit_prep_question_set_version"), nullable=False))
    creation_idempotency_key: str = Field(max_length=36)
    creation_payload_hmac_sha256: str = Field(max_length=64, repr=False, exclude=True)
    recipient_snapshot_ciphertext: str = Field(repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False))
    has_email_contact: bool = False
    has_sms_contact: bool = False
    physician_display_name: str = Field(max_length=160)
    practice_display_name: str = Field(max_length=200)
    lifecycle_state: str = Field(default="invited", max_length=32)
    scheduled_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    timezone_name: str = Field(max_length=64)
    schedule_generation: int = 1
    credential_generation: int = 1
    token_hash: str = Field(max_length=64, repr=False, exclude=True)
    credential_expires_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    credential_revoked_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))  # type: ignore
    confirmed_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))  # type: ignore
    confirmed_by_role: str | None = Field(default=None, max_length=16)
    confirmation_name_digest: str | None = Field(default=None, max_length=64, repr=False, exclude=True)
    started_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))  # type: ignore
    completed_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))  # type: ignore


class VisitPrepQuestionResponse(BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_response"), table=True):
    __table_args__ = (
        sa.CheckConstraint("submission_sequence BETWEEN 1 AND 5", name="vp_response_sequence_range"),
        sa.CheckConstraint("response_kind IN ('answered', 'skipped')", name="vp_response_kind_allowed"),
        sa.CheckConstraint("(response_kind = 'answered' AND answer_ciphertext IS NOT NULL) OR (response_kind = 'skipped' AND answer_ciphertext IS NULL)", name="vp_response_answer_shape"),
        sa.CheckConstraint(f"idempotency_key ~ {_UUID_RE}", name="vp_response_idempotency_shape"),
        sa.ForeignKeyConstraint(["visit_prep_id"], ["visit_prep.id"], name="fk_vp_response_prep", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_set_version_id", "question_id"], ["visit_prep_question.question_set_version_id", "visit_prep_question.id"], name="fk_vp_response_question", ondelete="RESTRICT"),
        sa.UniqueConstraint("visit_prep_id", "question_id", name="uq_vp_response_question"),
        sa.UniqueConstraint("visit_prep_id", "submission_sequence", name="uq_vp_response_sequence"),
        sa.UniqueConstraint("visit_prep_id", "idempotency_key", name="uq_vp_response_idempotency"),
    )
    visit_prep_id: TypeIDType = Field(sa_column=Column(TypeIDType("visit_prep"), nullable=False))
    question_set_version_id: TypeIDType = Field(sa_column=Column(TypeIDType("visit_prep_question_set_version"), nullable=False))
    question_id: TypeIDType = Field(sa_column=Column(TypeIDType("visit_prep_question"), nullable=False))
    submission_sequence: int
    response_kind: str = Field(max_length=16)
    answer_ciphertext: str | None = Field(default=None, repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=True))
    idempotency_key: str = Field(max_length=36)
    payload_hmac_sha256: str = Field(max_length=64, repr=False, exclude=True)
    recorded_at: datetime = Field(sa_type=sa.DateTime(timezone=True), sa_column_kwargs={"server_default": sa.func.now()})  # type: ignore


def _forbid_response_update(*_: object) -> None:
    raise RuntimeError("Visit Prep responses are append-only.")


sa.event.listen(VisitPrepQuestionResponse, "before_update", _forbid_response_update)
# fmt: on
