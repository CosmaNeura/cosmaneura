"""Provider-neutral coordination state for virtual-visit capture.

These records authorize and coordinate a capture source. They do not replace
Scribe's immutable consent evidence or store provider callback payloads. Raw
external identifiers and bearer capabilities must never be written here.
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

CAPTURE_SOURCE_KINDS = ("desktop_companion", "meeting_bot")
CAPTURE_SESSION_STATES = (
    "awaiting_consent",
    "ready",
    "starting",
    "recording",
    "stopping",
    "paused",
    "processing",
    "review_required",
    "transcript_ready",
    "cancelled",
    "failed",
    "delivered",
)
CAPTURE_CONSENT_STATES = ("pending", "granted", "revoked", "uncertain")
CAPTURE_AUTHORITY_ORIGINS = ("server_authorized", "preexisting_observed")
CAPTURE_LEASE_STATES = ("active", "uncertain", "closing", "closed")
CAPTURE_SEGMENT_CHANNELS = ("microphone", "system_audio", "mixed")
CAPTURE_SEGMENT_STATES = (
    "reserved",
    "capturing",
    "sealed",
    "uploaded",
    "failed",
    "discarded",
)
CAPTURE_OPERATION_STATES = (
    "reserved",
    "in_flight",
    "succeeded",
    "uncertain",
    "failed",
)
CAPTURE_EXTERNAL_RESOURCE_ROLES = (
    "capture_container",
    "recording_media",
    "transcript_artifact",
)
CAPTURE_RESOURCE_MAPPING_STATES = ("pending", "active", "uncertain", "retired")
CAPTURE_RESOURCE_CLEANUP_STATES = (
    "not_requested",
    "pending",
    "in_flight",
    "succeeded",
    "uncertain",
    "failed",
)


_SESSION_SCOPE_COLUMNS = ("session_id", "practice_id", "actor_id", "appointment_id")
_SESSION_SCOPE_TARGET = (
    "capture_session.id",
    "capture_session.practice_id",
    "capture_session.actor_id",
    "capture_session.appointment_id",
)


class CaptureSession(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("capture_session"),
    table=True,
):
    """One server-authorized or observed capture attempt for an appointment."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["appointment_id", "actor_id"],
            ["appointment.id", "appointment.doctor_id"],
            name="fk_capture_session_appointment_actor",
        ),
        sa.ForeignKeyConstraint(
            ["practice_id", "actor_id"],
            ["practice_member.practice_id", "practice_member.doctor_id"],
            name="fk_capture_session_practice_actor",
        ),
        sa.UniqueConstraint(
            "id",
            "practice_id",
            "actor_id",
            "appointment_id",
            name="uq_capture_session_id_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "practice_id",
            "actor_id",
            "appointment_id",
            "source_kind",
            name="uq_capture_session_id_scope_source",
        ),
        sa.CheckConstraint(
            "source_kind IN ('desktop_companion', 'meeting_bot')",
            name="ck_capture_session_source_kind",
        ),
        sa.CheckConstraint(
            "state IN ('awaiting_consent', 'ready', 'starting', 'recording', "
            "'stopping', 'paused', 'processing', 'review_required', "
            "'transcript_ready', 'cancelled', 'failed', 'delivered')",
            name="ck_capture_session_state",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_session_state_version",
        ),
        sa.CheckConstraint(
            "consent_state IN ('pending', 'granted', 'revoked', 'uncertain')",
            name="ck_capture_session_consent_state",
        ),
        sa.CheckConstraint(
            "authority_origin IN ('server_authorized', 'preexisting_observed')",
            name="ck_capture_session_authority_origin",
        ),
        sa.CheckConstraint(
            "consent_state <> 'granted' OR "
            "(scribe_consent_receipt_id IS NOT NULL AND "
            "consent_policy_version IS NOT NULL)",
            name="ck_capture_session_granted_consent_receipt",
        ),
        sa.CheckConstraint(
            "state NOT IN ('ready', 'starting', 'recording') OR "
            "consent_state = 'granted'",
            name="ck_capture_session_start_requires_consent",
        ),
        sa.CheckConstraint(
            "authority_origin <> 'preexisting_observed' OR "
            "state NOT IN ('ready', 'starting', 'recording')",
            name="ck_capture_session_observed_cannot_start",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR started_at IS NOT NULL",
            name="ck_capture_session_end_requires_start",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_capture_session_time_order",
        ),
        sa.Index(
            "ix_capture_session_appointment_created",
            "practice_id",
            "appointment_id",
            "created_at",
        ),
    )

    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    actor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    appointment_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("appointment"), nullable=False)
    )
    source_kind: str = Field(sa_column=Column(sa.String(32), nullable=False))
    state: str = Field(
        default="awaiting_consent",
        sa_column=Column(
            sa.String(32),
            nullable=False,
            server_default="awaiting_consent",
        ),
    )
    state_version: int = Field(
        default=0,
        sa_column=Column(sa.Integer(), nullable=False, server_default="0"),
    )
    consent_state: str = Field(
        default="pending",
        sa_column=Column(sa.String(16), nullable=False, server_default="pending"),
    )
    scribe_consent_receipt_id: UUID | None = Field(
        default=None,
        sa_column=Column(sa.Uuid(), nullable=True),
    )
    consent_policy_version: str | None = Field(default=None, max_length=32)
    processing_session_id: UUID | None = Field(
        default=None,
        sa_column=Column(sa.Uuid(), nullable=True, unique=True),
    )
    authority_origin: str = Field(
        default="server_authorized",
        sa_column=Column(
            sa.String(32),
            nullable=False,
            server_default="server_authorized",
        ),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class CaptureSourceLease(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("capture_source_lease"),
    table=True,
):
    """Exclusive capture-source lease; expiration alone never closes it."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            (*_SESSION_SCOPE_COLUMNS, "source_kind"),
            (*_SESSION_SCOPE_TARGET, "capture_session.source_kind"),
            name="fk_capture_source_lease_session_scope",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'uncertain', 'closing', 'closed')",
            name="ck_capture_source_lease_state",
        ),
        sa.CheckConstraint(
            "source_kind IN ('desktop_companion', 'meeting_bot')",
            name="ck_capture_source_lease_source_kind",
        ),
        sa.CheckConstraint(
            "lease_token_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_source_lease_token_digest",
        ),
        sa.CheckConstraint(
            "generation >= 0",
            name="ck_capture_source_lease_generation",
        ),
        sa.CheckConstraint(
            "control_deadline_at > acquired_at",
            name="ck_capture_source_lease_control_deadline",
        ),
        sa.CheckConstraint(
            "(state = 'closed') = (closed_at IS NOT NULL)",
            name="ck_capture_source_lease_closed_state",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR "
            "num_nonnulls(stop_proved_at, never_started_proved_at) = 1",
            name="ck_capture_source_lease_close_proof",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= acquired_at",
            name="ck_capture_source_lease_close_time",
        ),
        sa.CheckConstraint(
            "state NOT IN ('closing', 'closed') OR stop_requested_at IS NOT NULL",
            name="ck_capture_source_lease_stop_requested",
        ),
        sa.CheckConstraint(
            "stop_proved_at IS NULL OR stop_requested_at IS NOT NULL",
            name="ck_capture_source_lease_stop_proof_requested",
        ),
        sa.CheckConstraint(
            "never_started_proved_at IS NULL OR stop_requested_at IS NOT NULL",
            name="ck_capture_source_lease_never_started_proof_requested",
        ),
        sa.Index(
            "uq_capture_source_lease_open_appointment",
            "practice_id",
            "appointment_id",
            unique=True,
            postgresql_where=sa.text("closed_at IS NULL"),
        ),
        sa.Index(
            "ix_capture_source_lease_control_deadline",
            "state",
            "control_deadline_at",
        ),
        sa.UniqueConstraint(
            "practice_id",
            "appointment_id",
            "generation",
            name="uq_capture_source_lease_generation",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "practice_id",
            "actor_id",
            "appointment_id",
            name="uq_capture_source_lease_id_scope",
        ),
    )

    session_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("capture_session"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    actor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    appointment_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("appointment"), nullable=False)
    )
    source_kind: str = Field(sa_column=Column(sa.String(32), nullable=False))
    state: str = Field(
        default="active",
        sa_column=Column(sa.String(16), nullable=False, server_default="active"),
    )
    lease_token_digest: str = Field(
        sa_column=Column(sa.String(64), nullable=False, unique=True)
    )
    generation: int = Field(
        default=0,
        sa_column=Column(sa.Integer(), nullable=False, server_default="0"),
    )
    acquired_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    control_deadline_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    heartbeat_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    stop_requested_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    stop_proved_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    never_started_proved_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    closed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class CaptureSegment(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("capture_segment"),
    table=True,
):
    """One physical capture start; retries allocate a new ordinal and row."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            _SESSION_SCOPE_COLUMNS,
            _SESSION_SCOPE_TARGET,
            name="fk_capture_segment_session_scope",
        ),
        sa.UniqueConstraint(
            "session_id",
            "ordinal",
            name="uq_capture_segment_session_ordinal",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "practice_id",
            "actor_id",
            "appointment_id",
            name="uq_capture_segment_id_scope",
        ),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_capture_segment_ordinal",
        ),
        sa.CheckConstraint(
            "channel IN ('microphone', 'system_audio', 'mixed')",
            name="ck_capture_segment_channel",
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'capturing', 'sealed', 'uploaded', "
            "'failed', 'discarded')",
            name="ck_capture_segment_state",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR physical_started_at IS NOT NULL",
            name="ck_capture_segment_end_requires_start",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= physical_started_at",
            name="ck_capture_segment_time_order",
        ),
        sa.CheckConstraint(
            "byte_count IS NULL OR byte_count >= 0",
            name="ck_capture_segment_byte_count",
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_capture_segment_content_digest",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_segment_state_version",
        ),
        sa.CheckConstraint(
            "capability_digest IS NULL OR capability_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_segment_capability_digest",
        ),
        sa.CheckConstraint(
            "capability_expires_at IS NULL OR "
            "(capability_expires_at > capability_issued_at AND "
            "capability_expires_at <= capability_issued_at + INTERVAL '5 minutes')",
            name="ck_capture_segment_capability_lifetime",
        ),
        sa.CheckConstraint(
            "(capability_id IS NULL AND capability_digest IS NULL AND "
            "capability_issued_at IS NULL AND capability_expires_at IS NULL AND "
            "capability_consumed_at IS NULL AND capability_revoked_at IS NULL "
            "AND encrypted_capability IS NULL AND capability_purged_at IS NULL) OR "
            "(capability_id IS NOT NULL AND capability_digest IS NOT NULL AND "
            "capability_issued_at IS NOT NULL AND capability_expires_at IS NOT NULL)",
            name="ck_capture_segment_capability_complete",
        ),
        sa.CheckConstraint(
            "num_nonnulls(capability_consumed_at, capability_revoked_at) <= 1",
            name="ck_capture_segment_capability_terminal",
        ),
        sa.CheckConstraint(
            "capability_consumed_at IS NULL OR "
            "(capability_consumed_at >= capability_issued_at AND "
            "capability_consumed_at <= capability_expires_at)",
            name="ck_capture_segment_capability_consumed_in_window",
        ),
        sa.CheckConstraint(
            "capability_revoked_at IS NULL OR "
            "capability_revoked_at >= capability_issued_at",
            name="ck_capture_segment_capability_revoked_after_issue",
        ),
        sa.CheckConstraint(
            "encrypted_capability IS NULL OR "
            "(capability_consumed_at IS NULL AND capability_revoked_at IS NULL "
            "AND capability_purged_at IS NULL)",
            name="ck_capture_segment_capability_purge",
        ),
        sa.CheckConstraint(
            "capability_id IS NULL OR encrypted_capability IS NOT NULL OR "
            "capability_purged_at IS NOT NULL",
            name="ck_capture_segment_capability_ciphertext_purge",
        ),
        sa.CheckConstraint(
            "(capability_consumed_at IS NULL AND capability_revoked_at IS NULL) OR "
            "(encrypted_capability IS NULL AND capability_purged_at IS NOT NULL)",
            name="ck_capture_segment_capability_terminal_purge",
        ),
    )

    session_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("capture_session"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    actor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    appointment_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("appointment"), nullable=False)
    )
    ordinal: int
    channel: str = Field(sa_column=Column(sa.String(24), nullable=False))
    state: str = Field(
        default="reserved",
        sa_column=Column(sa.String(16), nullable=False, server_default="reserved"),
    )
    state_version: int = Field(
        default=0,
        sa_column=Column(sa.Integer(), nullable=False, server_default="0"),
    )
    capability_id: UUID | None = Field(
        default=None,
        sa_column=Column(sa.Uuid(), nullable=True, unique=True),
    )
    capability_digest: str | None = Field(
        default=None,
        sa_column=Column(sa.String(64), nullable=True, unique=True),
    )
    encrypted_capability: str | None = Field(sa_column=Column(sa.Text(), nullable=True))
    capability_issued_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    capability_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    capability_consumed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    capability_revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    capability_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    physical_started_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    content_sha256: str | None = Field(default=None, max_length=64)
    byte_count: int | None = None


class CaptureOperation(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("capture_operation"),
    table=True,
):
    """Durable idempotency and ambiguous-outcome fence for capture actions."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            _SESSION_SCOPE_COLUMNS,
            _SESSION_SCOPE_TARGET,
            name="fk_capture_operation_session_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "segment_id",
                "session_id",
                "practice_id",
                "actor_id",
                "appointment_id",
            ],
            [
                "capture_segment.id",
                "capture_segment.session_id",
                "capture_segment.practice_id",
                "capture_segment.actor_id",
                "capture_segment.appointment_id",
            ],
            name="fk_capture_operation_segment_scope",
        ),
        sa.UniqueConstraint(
            "practice_id",
            "caller_scope",
            "operation",
            "idempotency_key_digest",
            name="uq_capture_operation_idempotency",
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'in_flight', 'succeeded', 'uncertain', 'failed')",
            name="ck_capture_operation_state",
        ),
        sa.CheckConstraint(
            "idempotency_key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_idempotency_digest",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_request_digest",
        ),
        sa.CheckConstraint(
            "caller_principal_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_caller_digest",
        ),
        sa.CheckConstraint(
            "external_correlation_digest IS NULL OR "
            "external_correlation_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_correlation_digest",
        ),
        sa.CheckConstraint(
            "(external_correlation_digest IS NULL AND "
            "encrypted_external_correlation_ref IS NULL AND "
            "correlation_purged_at IS NULL) OR "
            "(external_correlation_digest IS NOT NULL AND "
            "((encrypted_external_correlation_ref IS NOT NULL AND "
            "correlation_purged_at IS NULL) OR "
            "(encrypted_external_correlation_ref IS NULL AND "
            "correlation_purged_at IS NOT NULL)))",
            name="ck_capture_operation_correlation_complete",
        ),
        sa.CheckConstraint(
            "replay_response_digest IS NULL OR "
            "replay_response_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_replay_digest",
        ),
        sa.CheckConstraint(
            "(replay_response_digest IS NULL AND encrypted_replay_response IS NULL "
            "AND replay_response_expires_at IS NULL AND "
            "replay_response_purged_at IS NULL) OR "
            "(replay_response_digest IS NOT NULL AND "
            "((encrypted_replay_response IS NOT NULL AND "
            "replay_response_expires_at IS NOT NULL AND "
            "replay_response_purged_at IS NULL) OR "
            "(encrypted_replay_response IS NULL AND "
            "replay_response_purged_at IS NOT NULL)))",
            name="ck_capture_operation_replay_complete",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_capture_operation_attempt_count",
        ),
        sa.CheckConstraint(
            "max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_capture_operation_attempt_limit",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR state IN ('succeeded', 'failed')",
            name="ck_capture_operation_completion_state",
        ),
        sa.Index(
            "ix_capture_operation_recovery",
            "state",
            "next_attempt_at",
            "lease_expires_at",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "practice_id",
            "actor_id",
            "appointment_id",
            name="uq_capture_operation_id_scope",
        ),
    )

    session_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("capture_session"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    actor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    appointment_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("appointment"), nullable=False)
    )
    segment_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("capture_segment"), nullable=True),
    )
    caller_scope: str = Field(max_length=64)
    caller_principal_digest: str = Field(max_length=64)
    operation: str = Field(max_length=64)
    idempotency_key_digest: str = Field(max_length=64)
    request_digest: str = Field(max_length=64)
    external_correlation_digest: str | None = Field(default=None, max_length=64)
    encrypted_external_correlation_ref: str | None = Field(
        sa_column=Column(sa.Text(), nullable=True)
    )
    correlation_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    replay_response_digest: str | None = Field(default=None, max_length=64)
    encrypted_replay_response: str | None = Field(
        sa_column=Column(sa.Text(), nullable=True)
    )
    replay_response_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    replay_response_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    state: str = Field(
        default="reserved",
        sa_column=Column(sa.String(16), nullable=False, server_default="reserved"),
    )
    attempt_count: int = Field(
        default=0,
        sa_column=Column(sa.Integer(), nullable=False, server_default="0"),
    )
    max_attempts: int = Field(
        default=5,
        sa_column=Column(sa.Integer(), nullable=False, server_default="5"),
    )
    next_attempt_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    last_attempt_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    last_error_code: str | None = Field(default=None, max_length=64)


class CaptureExternalResource(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("capture_resource"),
    table=True,
):
    """Encrypted adapter mapping with a durable keyed-HMAC tombstone."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            _SESSION_SCOPE_COLUMNS,
            _SESSION_SCOPE_TARGET,
            name="fk_capture_resource_session_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "segment_id",
                "session_id",
                "practice_id",
                "actor_id",
                "appointment_id",
            ],
            [
                "capture_segment.id",
                "capture_segment.session_id",
                "capture_segment.practice_id",
                "capture_segment.actor_id",
                "capture_segment.appointment_id",
            ],
            name="fk_capture_resource_segment_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "create_operation_id",
                "session_id",
                "practice_id",
                "actor_id",
                "appointment_id",
            ],
            [
                "capture_operation.id",
                "capture_operation.session_id",
                "capture_operation.practice_id",
                "capture_operation.actor_id",
                "capture_operation.appointment_id",
            ],
            name="fk_capture_resource_operation_scope",
        ),
        sa.UniqueConstraint(
            "adapter_profile",
            "role",
            "external_ref_digest",
            name="uq_capture_resource_adapter_role_digest",
        ),
        sa.CheckConstraint(
            "role IN ('capture_container', 'recording_media', 'transcript_artifact')",
            name="ck_capture_resource_role",
        ),
        sa.CheckConstraint(
            "mapping_state IN ('pending', 'active', 'uncertain', 'retired')",
            name="ck_capture_resource_mapping_state",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('not_requested', 'pending', 'in_flight', "
            "'succeeded', 'uncertain', 'failed')",
            name="ck_capture_resource_cleanup_state",
        ),
        sa.CheckConstraint(
            "cleanup_policy IN ('delete_on_terminal', 'delete_after_delivery', "
            "'retain_until_expiry')",
            name="ck_capture_resource_cleanup_policy",
        ),
        sa.CheckConstraint(
            "cleanup_policy <> 'retain_until_expiry' OR cleanup_due_at IS NOT NULL",
            name="ck_capture_resource_retention_due",
        ),
        sa.CheckConstraint(
            "external_ref_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_resource_ref_digest",
        ),
        sa.CheckConstraint(
            "(encrypted_external_ref IS NULL) = (reference_purged_at IS NOT NULL)",
            name="ck_capture_resource_ref_purge",
        ),
        sa.CheckConstraint(
            "(mapping_state = 'retired') = (retired_at IS NOT NULL)",
            name="ck_capture_resource_retired_state",
        ),
        sa.CheckConstraint(
            "cleanup_state <> 'succeeded' OR "
            "(cleaned_at IS NOT NULL AND reference_purged_at IS NOT NULL)",
            name="ck_capture_resource_cleanup_complete",
        ),
        sa.CheckConstraint(
            "cleanup_attempt_count >= 0",
            name="ck_capture_resource_cleanup_attempt_count",
        ),
        sa.CheckConstraint(
            "cleanup_result_digest IS NULL OR cleanup_result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_resource_cleanup_result_digest",
        ),
        sa.Index(
            "ix_capture_resource_cleanup",
            "cleanup_state",
            "cleanup_due_at",
        ),
    )

    session_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("capture_session"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    actor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    appointment_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("appointment"), nullable=False)
    )
    segment_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("capture_segment"), nullable=True),
    )
    create_operation_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("capture_operation"), nullable=True),
    )
    adapter_profile: str = Field(max_length=64)
    role: str = Field(max_length=32)
    external_ref_digest: str = Field(max_length=64)
    encrypted_external_ref: str | None = Field(
        sa_column=Column(sa.Text(), nullable=True)
    )
    mapping_state: str = Field(
        default="pending",
        sa_column=Column(sa.String(16), nullable=False, server_default="pending"),
    )
    cleanup_state: str = Field(
        default="not_requested",
        sa_column=Column(
            sa.String(16),
            nullable=False,
            server_default="not_requested",
        ),
    )
    cleanup_policy: str = Field(
        default="delete_on_terminal",
        sa_column=Column(
            sa.String(32),
            nullable=False,
            server_default="delete_on_terminal",
        ),
    )
    cleanup_due_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    cleanup_attempt_count: int = Field(
        default=0,
        sa_column=Column(sa.Integer(), nullable=False, server_default="0"),
    )
    cleanup_result_digest: str | None = Field(default=None, max_length=64)
    last_cleanup_error_code: str | None = Field(default=None, max_length=64)
    cleanup_requested_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    cleaned_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    retired_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    reference_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class CaptureLaunchAuthorization(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("capture_launch_auth"),
    table=True,
):
    """One-use launch and PKCE exchange; bearer secrets are never persisted."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            _SESSION_SCOPE_COLUMNS,
            _SESSION_SCOPE_TARGET,
            name="fk_capture_launch_auth_session_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_lease_id",
                "session_id",
                "practice_id",
                "actor_id",
                "appointment_id",
            ],
            [
                "capture_source_lease.id",
                "capture_source_lease.session_id",
                "capture_source_lease.practice_id",
                "capture_source_lease.actor_id",
                "capture_source_lease.appointment_id",
            ],
            name="fk_capture_launch_auth_source_lease_scope",
        ),
        sa.CheckConstraint(
            "state IN ('ticket_issued', 'code_issued', 'token_issued', "
            "'revoked', 'expired')",
            name="ck_capture_launch_auth_state",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_launch_auth_state_version",
        ),
        sa.CheckConstraint(
            "actor_binding_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_actor_digest",
        ),
        sa.CheckConstraint(
            "installation_thumbprint_digest IS NULL OR "
            "installation_thumbprint_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_installation_digest",
        ),
        sa.CheckConstraint(
            "redirect_uri_digest IS NULL OR redirect_uri_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_redirect_digest",
        ),
        sa.CheckConstraint(
            "launch_ticket_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_ticket_digest",
        ),
        sa.CheckConstraint(
            "(redirect_uri_digest IS NULL AND pkce_method IS NULL AND "
            "pkce_code_challenge IS NULL) OR "
            "(redirect_uri_digest IS NOT NULL AND pkce_method = 'S256' AND "
            "pkce_code_challenge ~ '^[A-Za-z0-9_-]{43}$')",
            name="ck_capture_launch_auth_pkce",
        ),
        sa.CheckConstraint(
            "ticket_expires_at > created_at AND "
            "ticket_expires_at <= created_at + INTERVAL '2 minutes'",
            name="ck_capture_launch_auth_ticket_lifetime",
        ),
        sa.CheckConstraint(
            "num_nonnulls(ticket_consumed_at, ticket_revoked_at) <= 1",
            name="ck_capture_launch_auth_ticket_terminal",
        ),
        sa.CheckConstraint(
            "ticket_consumed_at IS NULL OR "
            "(ticket_consumed_at >= created_at AND "
            "ticket_consumed_at <= ticket_expires_at)",
            name="ck_capture_launch_auth_ticket_consumed_in_window",
        ),
        sa.CheckConstraint(
            "(authorization_code_digest IS NULL AND "
            "authorization_code_issued_at IS NULL AND "
            "authorization_code_expires_at IS NULL AND "
            "authorization_code_consumed_at IS NULL AND "
            "authorization_code_revoked_at IS NULL AND "
            "redirect_uri_digest IS NULL AND pkce_method IS NULL AND "
            "pkce_code_challenge IS NULL AND "
            "installation_thumbprint_digest IS NULL) OR "
            "(ticket_consumed_at IS NOT NULL AND "
            "authorization_code_digest IS NOT NULL AND "
            "authorization_code_issued_at IS NOT NULL AND "
            "authorization_code_expires_at IS NOT NULL AND "
            "redirect_uri_digest IS NOT NULL AND pkce_method = 'S256' AND "
            "pkce_code_challenge IS NOT NULL AND "
            "installation_thumbprint_digest IS NOT NULL)",
            name="ck_capture_launch_auth_code_complete",
        ),
        sa.CheckConstraint(
            "authorization_code_digest IS NULL OR "
            "authorization_code_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_code_digest",
        ),
        sa.CheckConstraint(
            "authorization_code_expires_at IS NULL OR "
            "(authorization_code_expires_at > authorization_code_issued_at AND "
            "authorization_code_expires_at <= "
            "authorization_code_issued_at + INTERVAL '5 minutes')",
            name="ck_capture_launch_auth_code_lifetime",
        ),
        sa.CheckConstraint(
            "num_nonnulls(authorization_code_consumed_at, "
            "authorization_code_revoked_at) <= 1",
            name="ck_capture_launch_auth_code_terminal",
        ),
        sa.CheckConstraint(
            "authorization_code_consumed_at IS NULL OR "
            "(authorization_code_consumed_at >= authorization_code_issued_at AND "
            "authorization_code_consumed_at <= authorization_code_expires_at)",
            name="ck_capture_launch_auth_code_consumed_in_window",
        ),
        sa.CheckConstraint(
            "(desktop_token_digest IS NULL AND desktop_token_issued_at IS NULL "
            "AND desktop_token_expires_at IS NULL AND "
            "desktop_token_revoked_at IS NULL) OR "
            "(authorization_code_consumed_at IS NOT NULL AND "
            "desktop_token_digest IS NOT NULL AND "
            "desktop_token_issued_at IS NOT NULL AND "
            "desktop_token_expires_at IS NOT NULL)",
            name="ck_capture_launch_auth_token_complete",
        ),
        sa.CheckConstraint(
            "desktop_token_digest IS NULL OR desktop_token_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_token_digest",
        ),
        sa.CheckConstraint(
            "desktop_token_expires_at IS NULL OR "
            "(desktop_token_expires_at > desktop_token_issued_at AND "
            "desktop_token_expires_at <= "
            "desktop_token_issued_at + INTERVAL '6 hours')",
            name="ck_capture_launch_auth_token_lifetime",
        ),
    )

    session_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("capture_session"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    actor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    appointment_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("appointment"), nullable=False)
    )
    source_lease_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("capture_source_lease"), nullable=False)
    )
    state: str = Field(
        default="ticket_issued",
        sa_column=Column(sa.String(16), nullable=False, server_default="ticket_issued"),
    )
    state_version: int = Field(
        default=0,
        sa_column=Column(sa.Integer(), nullable=False, server_default="0"),
    )
    actor_binding_digest: str = Field(max_length=64)
    installation_thumbprint_digest: str | None = Field(default=None, max_length=64)
    redirect_uri_digest: str | None = Field(default=None, max_length=64)
    pkce_code_challenge: str | None = Field(default=None, max_length=43)
    pkce_method: str | None = Field(
        default=None,
        sa_column=Column(sa.String(8), nullable=True),
    )
    launch_ticket_digest: str = Field(
        sa_column=Column(sa.String(64), nullable=False, unique=True)
    )
    ticket_expires_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    ticket_consumed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    ticket_revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    authorization_code_digest: str | None = Field(
        default=None,
        sa_column=Column(sa.String(64), nullable=True, unique=True),
    )
    authorization_code_issued_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    authorization_code_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    authorization_code_consumed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    authorization_code_revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    desktop_token_digest: str | None = Field(
        default=None,
        sa_column=Column(sa.String(64), nullable=True, unique=True),
    )
    desktop_token_issued_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    desktop_token_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    desktop_token_revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class CaptureEvent(
    BaseModel,
    TypeIDMixin("capture_event"),
    table=True,
):
    """Append-only, server-timestamped capture transition evidence."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            _SESSION_SCOPE_COLUMNS,
            _SESSION_SCOPE_TARGET,
            name="fk_capture_event_session_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "segment_id",
                "session_id",
                "practice_id",
                "actor_id",
                "appointment_id",
            ],
            [
                "capture_segment.id",
                "capture_segment.session_id",
                "capture_segment.practice_id",
                "capture_segment.actor_id",
                "capture_segment.appointment_id",
            ],
            name="fk_capture_event_segment_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "operation_id",
                "session_id",
                "practice_id",
                "actor_id",
                "appointment_id",
            ],
            [
                "capture_operation.id",
                "capture_operation.session_id",
                "capture_operation.practice_id",
                "capture_operation.actor_id",
                "capture_operation.appointment_id",
            ],
            name="fk_capture_event_operation_scope",
        ),
        sa.UniqueConstraint(
            "session_id",
            "local_sequence",
            name="uq_capture_event_session_sequence",
        ),
        sa.CheckConstraint(
            "local_sequence >= 0",
            name="ck_capture_event_local_sequence",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_event_state_version",
        ),
        sa.CheckConstraint(
            "session_state IN ('awaiting_consent', 'ready', 'starting', "
            "'recording', 'stopping', 'paused', 'processing', "
            "'review_required', 'transcript_ready', 'cancelled', 'failed', "
            "'delivered')",
            name="ck_capture_event_session_state",
        ),
        sa.CheckConstraint(
            "source_event_digest IS NULL OR source_event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_event_source_digest",
        ),
        sa.Index(
            "ix_capture_event_recorded",
            "practice_id",
            "recorded_at",
        ),
    )

    session_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("capture_session"), nullable=False)
    )
    practice_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice"), nullable=False)
    )
    actor_id: TypeIDType = Field(sa_column=Column(TypeIDType("doctor"), nullable=False))
    appointment_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("appointment"), nullable=False)
    )
    segment_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("capture_segment"), nullable=True),
    )
    operation_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("capture_operation"), nullable=True),
    )
    external_webhook_delivery_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("webhook_delivery"),
            sa.ForeignKey("external_webhook_delivery.id"),
            nullable=True,
        ),
    )
    local_sequence: int
    event_type: str = Field(max_length=64)
    state_version: int
    session_state: str = Field(max_length=32)
    source_event_digest: str | None = Field(default=None, max_length=64)
    provider_observed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    recorded_at: datetime = Field(
        sa_column=Column(
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        )
    )
