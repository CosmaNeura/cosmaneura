"""Dormant Visit Prep aggregate, schedule, credential, and legal-hold schema.

This expand layer intentionally contains no creation, public-token, delivery,
answer, or dashboard behavior. Later services must lock the root ``VisitPrep``
row before every mutation and continue to enforce the release-control seam.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrep(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep"),
    table=True,
):
    """One physician-owned asynchronous Visit Prep invitation and record."""

    __table_args__ = (
        sa.CheckConstraint(
            "creation_source ~ '^[a-z][a-z0-9_]{0,31}$'",
            name="creation_source_shape",
        ),
        sa.CheckConstraint(
            "creation_idempotency_key ~ "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="creation_idempotency_key_shape",
        ),
        sa.CheckConstraint(
            "creation_payload_hmac_sha256 ~ '^[0-9a-f]{64}$'",
            name="creation_payload_hmac_sha256_shape",
        ),
        sa.CheckConstraint(
            "recipient_snapshot_format_version = 1",
            name="recipient_snapshot_format_version",
        ),
        sa.CheckConstraint(
            "octet_length(recipient_snapshot_ciphertext) BETWEEN 1 AND 16384",
            name="recipient_snapshot_ciphertext_size",
        ),
        sa.CheckConstraint(
            "has_email_contact IS TRUE OR has_sms_contact IS TRUE",
            name="recipient_has_delivery_contact",
        ),
        sa.CheckConstraint(
            "physician_display_name = btrim(physician_display_name) AND "
            "char_length(physician_display_name) BETWEEN 1 AND 160",
            name="physician_display_name_normalized",
        ),
        sa.CheckConstraint(
            "practice_display_name = btrim(practice_display_name) AND "
            "char_length(practice_display_name) BETWEEN 1 AND 200",
            name="practice_display_name_normalized",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN "
            "('invited', 'confirmed', 'in_progress', 'completed', 'misdirected')",
            name="lifecycle_state_allowed",
        ),
        sa.CheckConstraint(
            "(confirmed_at IS NULL AND confirmed_by_role IS NULL) OR "
            "(confirmed_at IS NOT NULL AND "
            "confirmed_by_role IN ('patient', 'caregiver'))",
            name="confirmation_complete",
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR confirmed_at >= created_at",
            name="confirmation_not_before_creation",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR "
            "(confirmed_at IS NOT NULL AND started_at >= confirmed_at)",
            name="start_after_confirmation",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR "
            "(started_at IS NOT NULL AND completed_at >= started_at)",
            name="completion_after_start",
        ),
        sa.CheckConstraint(
            "misdirected_at IS NULL OR "
            "(confirmed_at IS NOT NULL "
            "AND misdirected_at >= COALESCE(completed_at, started_at, confirmed_at) "
            "AND revoked_at IS NOT NULL AND revoked_at <= misdirected_at)",
            name="misdirection_after_interaction",
        ),
        sa.CheckConstraint(
            "(lifecycle_state = 'invited' AND confirmed_at IS NULL "
            "AND started_at IS NULL AND completed_at IS NULL "
            "AND misdirected_at IS NULL) OR "
            "(lifecycle_state = 'confirmed' AND confirmed_at IS NOT NULL "
            "AND started_at IS NULL AND completed_at IS NULL "
            "AND misdirected_at IS NULL) OR "
            "(lifecycle_state = 'in_progress' AND confirmed_at IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND misdirected_at IS NULL) OR "
            "(lifecycle_state = 'completed' AND confirmed_at IS NOT NULL "
            "AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND misdirected_at IS NULL) OR "
            "(lifecycle_state = 'misdirected' AND confirmed_at IS NOT NULL "
            "AND misdirected_at IS NOT NULL)",
            name="lifecycle_timestamps_match_state",
        ),
        sa.CheckConstraint(
            "(retention_policy_version IS NULL AND retain_until IS NULL) OR "
            "(confirmed_at IS NOT NULL AND retention_policy_version IS NOT NULL "
            "AND retention_policy_version = btrim(retention_policy_version) "
            "AND char_length(retention_policy_version) BETWEEN 1 AND 64 "
            "AND retain_until IS NOT NULL AND retain_until >= confirmed_at)",
            name="retention_snapshot_complete",
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR "
            "(retention_policy_version IS NOT NULL AND retain_until IS NOT NULL)",
            name="confirmed_has_retention_snapshot",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_ref IS NULL "
            "AND revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_at >= created_at "
            "AND revoked_by_ref IS NOT NULL "
            "AND revoked_by_ref = btrim(revoked_by_ref) "
            "AND char_length(revoked_by_ref) BETWEEN 1 AND 160 "
            "AND revocation_reason IS NOT NULL "
            "AND revocation_reason ~ '^[a-z][a-z0-9_]{0,63}$')",
            name="revocation_complete",
        ),
        sa.CheckConstraint(
            "(misdirected_at IS NULL AND misdirected_reason IS NULL "
            "AND misdirected_incident_ref IS NULL "
            "AND misdirected_disposition IS NULL "
            "AND misdirected_disposition_at IS NULL "
            "AND misdirected_disposition_by_ref IS NULL "
            "AND misdirected_disposition_until IS NULL) OR "
            "(misdirected_at IS NOT NULL AND misdirected_reason IS NOT NULL "
            "AND misdirected_reason ~ '^[a-z][a-z0-9_]{0,63}$' "
            "AND misdirected_incident_ref IS NOT NULL "
            "AND misdirected_incident_ref = btrim(misdirected_incident_ref) "
            "AND char_length(misdirected_incident_ref) BETWEEN 1 AND 160 "
            "AND misdirected_disposition IS NOT NULL "
            "AND ((misdirected_disposition = 'pending_review' "
            "AND misdirected_disposition_at IS NULL "
            "AND misdirected_disposition_by_ref IS NULL "
            "AND misdirected_disposition_until IS NULL) OR "
            "(misdirected_disposition IN ('retain_until', 'purge_approved') "
            "AND misdirected_disposition_at IS NOT NULL "
            "AND misdirected_disposition_at >= misdirected_at "
            "AND misdirected_disposition_by_ref IS NOT NULL "
            "AND misdirected_disposition_by_ref = "
            "btrim(misdirected_disposition_by_ref) "
            "AND char_length(misdirected_disposition_by_ref) BETWEEN 1 AND 160 "
            "AND misdirected_disposition_until IS NOT NULL "
            "AND misdirected_disposition_until >= misdirected_disposition_at) OR "
            "(misdirected_disposition = 'legal_hold' "
            "AND misdirected_disposition_at IS NOT NULL "
            "AND misdirected_disposition_at >= misdirected_at "
            "AND misdirected_disposition_by_ref IS NOT NULL "
            "AND misdirected_disposition_by_ref = "
            "btrim(misdirected_disposition_by_ref) "
            "AND char_length(misdirected_disposition_by_ref) BETWEEN 1 AND 160 "
            "AND misdirected_disposition_until IS NULL)))",
            name="misdirection_complete",
        ),
        sa.CheckConstraint(
            "lifecycle_state <> 'misdirected' OR revoked_at IS NOT NULL",
            name="misdirected_is_revoked",
        ),
        sa.CheckConstraint(
            "(patient_id IS NULL AND patient_linked_at IS NULL "
            "AND patient_linked_by_ref IS NULL) OR "
            "(patient_id IS NOT NULL AND patient_linked_at IS NOT NULL "
            "AND patient_linked_at >= created_at "
            "AND patient_linked_by_ref IS NOT NULL "
            "AND patient_linked_by_ref = btrim(patient_linked_by_ref) "
            "AND char_length(patient_linked_by_ref) BETWEEN 1 AND 160)",
            name="patient_link_complete",
        ),
        sa.CheckConstraint(
            "(appointment_id IS NULL AND appointment_linked_at IS NULL "
            "AND appointment_linked_by_ref IS NULL) OR "
            "(appointment_id IS NOT NULL AND patient_id IS NOT NULL "
            "AND appointment_linked_at IS NOT NULL "
            "AND appointment_linked_at >= created_at "
            "AND appointment_linked_at >= patient_linked_at "
            "AND appointment_linked_by_ref IS NOT NULL "
            "AND appointment_linked_by_ref = btrim(appointment_linked_by_ref) "
            "AND char_length(appointment_linked_by_ref) BETWEEN 1 AND 160)",
            name="appointment_link_complete",
        ),
        sa.ForeignKeyConstraint(
            ["owner_practice_member_id", "practice_id", "owner_doctor_id"],
            [
                "practice_member.id",
                "practice_member.practice_id",
                "practice_member.doctor_id",
            ],
            name="fk_visit_prep_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_set_version_id", "owner_practice_member_id"],
            [
                "visit_prep_question_set_version.id",
                "visit_prep_question_set_version.owner_practice_member_id",
            ],
            name="fk_visit_prep_question_version_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "owner_doctor_id"],
            ["patient.id", "patient.doctor_id"],
            name="fk_visit_prep_patient_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id", "owner_doctor_id", "patient_id"],
            ["appointment.id", "appointment.doctor_id", "appointment.patient_id"],
            name="fk_visit_prep_appointment_owner_patient",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "owner_practice_member_id",
            name="uq_visit_prep_id_owner",
        ),
        sa.UniqueConstraint(
            "id",
            "owner_practice_member_id",
            "question_set_version_id",
            name="uq_visit_prep_id_owner_question_version",
        ),
        sa.UniqueConstraint(
            "owner_practice_member_id",
            "creation_idempotency_key",
            name="uq_visit_prep_owner_creation_key",
        ),
        sa.Index(
            "visit_prep_owner_created_idx",
            "owner_practice_member_id",
            "created_at",
        ),
        sa.Index("visit_prep_practice_created_idx", "practice_id", "created_at"),
        sa.Index(
            "visit_prep_patient_idx",
            "patient_id",
            postgresql_where=sa.text("patient_id IS NOT NULL"),
        ),
        sa.Index(
            "visit_prep_appointment_idx",
            "appointment_id",
            postgresql_where=sa.text("appointment_id IS NOT NULL"),
        ),
        sa.Index(
            "visit_prep_retain_until_idx",
            "retain_until",
            postgresql_where=sa.text("retain_until IS NOT NULL"),
        ),
        sa.Index(
            "visit_prep_misdirected_idx",
            "misdirected_at",
            postgresql_where=sa.text("misdirected_at IS NOT NULL"),
        ),
        sa.Index(
            "visit_prep_misdirected_disposition_until_idx",
            "misdirected_disposition_until",
            postgresql_where=sa.text(
                "lifecycle_state = 'misdirected' "
                "AND misdirected_disposition_until IS NOT NULL"
            ),
        ),
    )

    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    owner_doctor_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("doctor"), nullable=False)
    )
    question_set_version_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_question_set_version"), nullable=False)
    )
    patient_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("patient"), nullable=True),
    )
    appointment_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("appointment"), nullable=True),
    )

    creation_source: str = Field(max_length=32)
    creation_idempotency_key: str = Field(max_length=36)
    # This must be a context-separated keyed HMAC, not a plain digest: the
    # creation payload contains contact data with a guessable input space.
    creation_payload_hmac_sha256: str = Field(
        max_length=64,
        repr=False,
        exclude=True,
    )

    # Explicit ciphertext avoids decryption during ORM materialisation, so a
    # later service can quarantine corrupt or retired-key envelopes safely.
    recipient_snapshot_ciphertext: str = Field(
        repr=False,
        exclude=True,
        sa_column=Column(sa.Text(), nullable=False),
    )
    recipient_snapshot_format_version: int = Field(
        default=1,
        sa_column=Column(sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    has_email_contact: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    has_sms_contact: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    physician_display_name: str = Field(max_length=160)
    practice_display_name: str = Field(max_length=200)

    lifecycle_state: str = Field(
        default="invited",
        sa_column=Column(
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'invited'"),
        ),
    )
    confirmed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    confirmed_by_role: str | None = Field(default=None, max_length=16)
    started_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    misdirected_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    misdirected_reason: str | None = Field(default=None, max_length=64)
    misdirected_incident_ref: str | None = Field(default=None, max_length=160)
    misdirected_disposition: str | None = Field(default=None, max_length=24)
    misdirected_disposition_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    misdirected_disposition_by_ref: str | None = Field(default=None, max_length=160)
    misdirected_disposition_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )

    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by_ref: str | None = Field(default=None, max_length=160)
    revocation_reason: str | None = Field(default=None, max_length=64)

    retention_policy_version: str | None = Field(default=None, max_length=64)
    retain_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )

    patient_linked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    patient_linked_by_ref: str | None = Field(default=None, max_length=160)
    appointment_linked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    appointment_linked_by_ref: str | None = Field(default=None, max_length=160)


class VisitPrepScheduleGeneration(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_schedule"),
    table=True,
):
    """Immutable schedule history; the greatest generation is current."""

    __table_args__ = (
        sa.CheckConstraint("generation > 0", name="generation_positive"),
        sa.CheckConstraint(
            "timezone_name = btrim(timezone_name) "
            "AND char_length(timezone_name) BETWEEN 1 AND 64 "
            "AND timezone_name ~ "
            "'^[A-Za-z][A-Za-z0-9._+-]*(/[A-Za-z0-9._+-]+)*$'",
            name="timezone_name_shape",
        ),
        sa.CheckConstraint(
            "unused_purge_at = scheduled_at + interval '720 hours'",
            name="unused_purge_at_policy",
        ),
        sa.CheckConstraint(
            "unused_hard_purge_at = scheduled_at + interval '2160 hours'",
            name="unused_hard_purge_at_policy",
        ),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id"],
            ["visit_prep.id", "visit_prep.owner_practice_member_id"],
            name="fk_visit_prep_schedule_owner",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "visit_prep_id",
            "owner_practice_member_id",
            name="uq_visit_prep_schedule_id_prep_owner",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "generation",
            name="uq_visit_prep_schedule_generation",
        ),
        sa.Index("visit_prep_schedule_unused_purge_idx", "unused_purge_at"),
        sa.Index(
            "visit_prep_schedule_unused_hard_purge_idx",
            "unused_hard_purge_at",
        ),
    )

    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    generation: int
    scheduled_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    timezone_name: str = Field(max_length=64)
    unused_purge_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    unused_hard_purge_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True)  # type: ignore
    )


class VisitPrepCredentialGeneration(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_credential"),
    table=True,
):
    """One verifier-only bearer generation for an exact schedule snapshot."""

    __table_args__ = (
        sa.CheckConstraint("generation > 0", name="generation_positive"),
        sa.CheckConstraint(
            "token_format_version = 1",
            name="token_format_version",
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="token_hash_shape",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="expiry_after_issue",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_ref IS NULL "
            "AND revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_at >= issued_at "
            "AND revoked_by_ref IS NOT NULL "
            "AND revoked_by_ref = btrim(revoked_by_ref) "
            "AND char_length(revoked_by_ref) BETWEEN 1 AND 160 "
            "AND revocation_reason IS NOT NULL "
            "AND revocation_reason ~ '^[a-z][a-z0-9_]{0,63}$')",
            name="revocation_complete",
        ),
        sa.ForeignKeyConstraint(
            [
                "schedule_generation_id",
                "visit_prep_id",
                "owner_practice_member_id",
            ],
            [
                "visit_prep_schedule_generation.id",
                "visit_prep_schedule_generation.visit_prep_id",
                "visit_prep_schedule_generation.owner_practice_member_id",
            ],
            name="fk_visit_prep_credential_schedule_owner",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "generation",
            name="uq_visit_prep_credential_generation",
        ),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_visit_prep_credential_token_hash",
        ),
        sa.Index(
            "uq_visit_prep_active_credential",
            "visit_prep_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "visit_prep_credential_schedule_idx",
            "schedule_generation_id",
        ),
        sa.Index("visit_prep_credential_expiry_idx", "expires_at"),
    )

    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    schedule_generation_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_schedule"), nullable=False)
    )
    generation: int
    token_format_version: int = Field(
        default=1,
        sa_column=Column(sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    token_hash: str = Field(max_length=64, repr=False, exclude=True)
    issued_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    expires_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by_ref: str | None = Field(default=None, max_length=160)
    revocation_reason: str | None = Field(default=None, max_length=64)


class VisitPrepLegalHold(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_legal_hold"),
    table=True,
):
    """Append-only evidence for a legal hold and its optional later release."""

    __table_args__ = (
        sa.CheckConstraint("generation > 0", name="generation_positive"),
        sa.CheckConstraint(
            "placed_by_ref = btrim(placed_by_ref) "
            "AND char_length(placed_by_ref) BETWEEN 1 AND 160",
            name="placed_by_ref_normalized",
        ),
        sa.CheckConstraint(
            "placement_reason ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="placement_reason_shape",
        ),
        sa.CheckConstraint(
            "(released_at IS NULL AND released_by_ref IS NULL "
            "AND release_reason IS NULL) OR "
            "(released_at IS NOT NULL AND released_at >= placed_at "
            "AND released_by_ref IS NOT NULL "
            "AND released_by_ref = btrim(released_by_ref) "
            "AND char_length(released_by_ref) BETWEEN 1 AND 160 "
            "AND release_reason IS NOT NULL "
            "AND release_reason ~ '^[a-z][a-z0-9_]{0,63}$')",
            name="release_complete",
        ),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id"],
            ["visit_prep.id", "visit_prep.owner_practice_member_id"],
            name="fk_visit_prep_legal_hold_owner",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "generation",
            name="uq_visit_prep_legal_hold_generation",
        ),
        sa.Index(
            "uq_visit_prep_active_legal_hold",
            "visit_prep_id",
            unique=True,
            postgresql_where=sa.text("released_at IS NULL"),
        ),
    )

    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    generation: int
    placed_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    placed_by_ref: str = Field(max_length=160)
    placement_reason: str = Field(max_length=64)
    released_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    released_by_ref: str | None = Field(default=None, max_length=160)
    release_reason: str | None = Field(default=None, max_length=64)
