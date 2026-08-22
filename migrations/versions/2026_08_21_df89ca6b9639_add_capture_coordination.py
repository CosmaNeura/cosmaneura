"""add provider-neutral capture coordination

Revision ID: df89ca6b9639
Revises: e4b9a63f210d
Create Date: 2026-08-21 22:00:00.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "df89ca6b9639"
down_revision: Union[str, None] = "e4b9a63f210d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id_column(prefix: str) -> sa.Column:
    return sa.Column(
        "id",
        activemodel.types.typeid.TypeIDType(prefix=prefix),
        nullable=False,
        comment=f"TypeID with prefix: {prefix}",
    )


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )


def _scope_columns() -> tuple[sa.Column, sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column(
            "session_id",
            activemodel.types.typeid.TypeIDType(prefix="capture_session"),
            nullable=False,
        ),
        sa.Column(
            "practice_id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column(
            "appointment_id",
            activemodel.types.typeid.TypeIDType(prefix="appointment"),
            nullable=False,
        ),
    )


def _scope_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["session_id", "practice_id", "actor_id", "appointment_id"],
        [
            "capture_session.id",
            "capture_session.practice_id",
            "capture_session.actor_id",
            "capture_session.appointment_id",
        ],
        name=name,
    )


def upgrade() -> None:
    op.add_column(
        "appointment",
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Server-recorded cancellation time; capture launch fails closed "
                "when set."
            ),
        ),
    )
    op.create_unique_constraint(
        "uq_appointment_id_doctor_id_capture",
        "appointment",
        ["id", "doctor_id"],
    )
    op.create_unique_constraint(
        "uq_practice_member_practice_doctor_capture",
        "practice_member",
        ["practice_id", "doctor_id"],
    )

    op.create_table(
        "capture_session",
        _id_column("capture_session"),
        *_timestamp_columns(),
        sa.Column(
            "practice_id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column(
            "appointment_id",
            activemodel.types.typeid.TypeIDType(prefix="appointment"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default="awaiting_consent",
            nullable=False,
        ),
        sa.Column(
            "state_version", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "consent_state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("scribe_consent_receipt_id", sa.Uuid(), nullable=True),
        sa.Column("consent_policy_version", sa.String(length=32), nullable=True),
        sa.Column("processing_session_id", sa.Uuid(), nullable=True, unique=True),
        sa.Column(
            "authority_origin",
            sa.String(length=32),
            server_default="server_authorized",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "authority_origin IN ('server_authorized', 'preexisting_observed')",
            name="ck_capture_session_authority_origin",
        ),
        sa.CheckConstraint(
            "authority_origin <> 'preexisting_observed' OR "
            "state NOT IN ('ready', 'starting', 'recording')",
            name="ck_capture_session_observed_cannot_start",
        ),
        sa.CheckConstraint(
            "consent_state IN ('pending', 'granted', 'revoked', 'uncertain')",
            name="ck_capture_session_consent_state",
        ),
        sa.CheckConstraint(
            "consent_state <> 'granted' OR "
            "(scribe_consent_receipt_id IS NOT NULL AND "
            "consent_policy_version IS NOT NULL)",
            name="ck_capture_session_granted_consent_receipt",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR started_at IS NOT NULL",
            name="ck_capture_session_end_requires_start",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_capture_session_time_order",
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
            "state NOT IN ('ready', 'starting', 'recording') OR "
            "consent_state = 'granted'",
            name="ck_capture_session_start_requires_consent",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_session_state_version",
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("capture_session_pkey")),
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
        comment=(
            "Provider-neutral capture coordination. Consent content remains in "
            "the authoritative registries; only the Scribe receipt UUID is stored."
        ),
    )
    op.create_index(
        "ix_capture_session_appointment_created",
        "capture_session",
        ["practice_id", "appointment_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "capture_source_lease",
        _id_column("capture_source_lease"),
        *_timestamp_columns(),
        *_scope_columns(),
        sa.Column(
            "state", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "lease_token_digest", sa.String(length=64), nullable=False, unique=True
        ),
        sa.Column(
            "generation", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("control_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_proved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "never_started_proved_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "closed_at IS NULL OR "
            "num_nonnulls(stop_proved_at, never_started_proved_at) = 1",
            name="ck_capture_source_lease_close_proof",
        ),
        sa.CheckConstraint(
            "(state = 'closed') = (closed_at IS NOT NULL)",
            name="ck_capture_source_lease_closed_state",
        ),
        sa.CheckConstraint(
            "control_deadline_at > acquired_at",
            name="ck_capture_source_lease_control_deadline",
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
            "source_kind IN ('desktop_companion', 'meeting_bot')",
            name="ck_capture_source_lease_source_kind",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'uncertain', 'closing', 'closed')",
            name="ck_capture_source_lease_state",
        ),
        sa.ForeignKeyConstraint(
            [
                "session_id",
                "practice_id",
                "actor_id",
                "appointment_id",
                "source_kind",
            ],
            [
                "capture_session.id",
                "capture_session.practice_id",
                "capture_session.actor_id",
                "capture_session.appointment_id",
                "capture_session.source_kind",
            ],
            name="fk_capture_source_lease_session_scope",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("capture_source_lease_pkey")),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "practice_id",
            "actor_id",
            "appointment_id",
            name="uq_capture_source_lease_id_scope",
        ),
        sa.UniqueConstraint(
            "practice_id",
            "appointment_id",
            "generation",
            name="uq_capture_source_lease_generation",
        ),
        comment=(
            "Exclusive capture-source lease. Expiry is evidence for recovery, "
            "never proof that a source stopped."
        ),
    )
    op.create_index(
        "ix_capture_source_lease_control_deadline",
        "capture_source_lease",
        ["state", "control_deadline_at"],
        unique=False,
    )
    op.create_index(
        "uq_capture_source_lease_open_appointment",
        "capture_source_lease",
        ["practice_id", "appointment_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    op.create_table(
        "capture_segment",
        _id_column("capture_segment"),
        *_timestamp_columns(),
        *_scope_columns(),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default="reserved", nullable=False
        ),
        sa.Column(
            "state_version", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("capability_id", sa.Uuid(), nullable=True, unique=True),
        sa.Column(
            "capability_digest", sa.String(length=64), nullable=True, unique=True
        ),
        sa.Column("encrypted_capability", sa.Text(), nullable=True),
        sa.Column("capability_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "capability_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "capability_consumed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "capability_revoked_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("capability_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("physical_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_count", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "byte_count IS NULL OR byte_count >= 0",
            name="ck_capture_segment_byte_count",
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
            "channel IN ('microphone', 'system_audio', 'mixed')",
            name="ck_capture_segment_channel",
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_capture_segment_content_digest",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR physical_started_at IS NOT NULL",
            name="ck_capture_segment_end_requires_start",
        ),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_capture_segment_ordinal",
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'capturing', 'sealed', 'uploaded', "
            "'failed', 'discarded')",
            name="ck_capture_segment_state",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_segment_state_version",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= physical_started_at",
            name="ck_capture_segment_time_order",
        ),
        _scope_fk("fk_capture_segment_session_scope"),
        sa.PrimaryKeyConstraint("id", name=op.f("capture_segment_pkey")),
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
        comment="Each row represents exactly one physical capture start.",
    )

    op.create_table(
        "capture_operation",
        _id_column("capture_operation"),
        *_timestamp_columns(),
        *_scope_columns(),
        sa.Column(
            "segment_id",
            activemodel.types.typeid.TypeIDType(prefix="capture_segment"),
            nullable=True,
        ),
        sa.Column("caller_scope", sa.String(length=64), nullable=False),
        sa.Column("caller_principal_digest", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("external_correlation_digest", sa.String(length=64), nullable=True),
        sa.Column("encrypted_external_correlation_ref", sa.Text(), nullable=True),
        sa.Column("correlation_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_response_digest", sa.String(length=64), nullable=True),
        sa.Column("encrypted_replay_response", sa.Text(), nullable=True),
        sa.Column(
            "replay_response_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "replay_response_purged_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "state", sa.String(length=16), server_default="reserved", nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_capture_operation_attempt_count",
        ),
        sa.CheckConstraint(
            "max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_capture_operation_attempt_limit",
        ),
        sa.CheckConstraint(
            "caller_principal_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_caller_digest",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR state IN ('succeeded', 'failed')",
            name="ck_capture_operation_completion_state",
        ),
        sa.CheckConstraint(
            "idempotency_key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_idempotency_digest",
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
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_operation_request_digest",
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
            "state IN ('reserved', 'in_flight', 'succeeded', 'uncertain', 'failed')",
            name="ck_capture_operation_state",
        ),
        _scope_fk("fk_capture_operation_session_scope"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("capture_operation_pkey")),
        sa.UniqueConstraint(
            "practice_id",
            "caller_scope",
            "operation",
            "idempotency_key_digest",
            name="uq_capture_operation_idempotency",
        ),
        sa.UniqueConstraint(
            "id",
            "session_id",
            "practice_id",
            "actor_id",
            "appointment_id",
            name="uq_capture_operation_id_scope",
        ),
        comment="Durable idempotency and ambiguous-outcome fence.",
    )
    op.create_index(
        "ix_capture_operation_recovery",
        "capture_operation",
        ["state", "next_attempt_at", "lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "capture_external_resource",
        _id_column("capture_resource"),
        *_timestamp_columns(),
        *_scope_columns(),
        sa.Column(
            "segment_id",
            activemodel.types.typeid.TypeIDType(prefix="capture_segment"),
            nullable=True,
        ),
        sa.Column(
            "create_operation_id",
            activemodel.types.typeid.TypeIDType(prefix="capture_operation"),
            nullable=True,
        ),
        sa.Column("adapter_profile", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("external_ref_digest", sa.String(length=64), nullable=False),
        sa.Column("encrypted_external_ref", sa.Text(), nullable=True),
        sa.Column(
            "mapping_state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "cleanup_state",
            sa.String(length=16),
            server_default="not_requested",
            nullable=False,
        ),
        sa.Column(
            "cleanup_policy",
            sa.String(length=32),
            server_default="delete_on_terminal",
            nullable=False,
        ),
        sa.Column("cleanup_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cleanup_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("cleanup_result_digest", sa.String(length=64), nullable=True),
        sa.Column("last_cleanup_error_code", sa.String(length=64), nullable=True),
        sa.Column("cleanup_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference_purged_at", sa.DateTime(timezone=True), nullable=True),
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
            "cleanup_policy IN ('delete_on_terminal', 'delete_after_delivery', "
            "'retain_until_expiry')",
            name="ck_capture_resource_cleanup_policy",
        ),
        sa.CheckConstraint(
            "cleanup_policy <> 'retain_until_expiry' OR cleanup_due_at IS NOT NULL",
            name="ck_capture_resource_retention_due",
        ),
        sa.CheckConstraint(
            "cleanup_result_digest IS NULL OR "
            "cleanup_result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_resource_cleanup_result_digest",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('not_requested', 'pending', 'in_flight', "
            "'succeeded', 'uncertain', 'failed')",
            name="ck_capture_resource_cleanup_state",
        ),
        sa.CheckConstraint(
            "external_ref_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_resource_ref_digest",
        ),
        sa.CheckConstraint(
            "mapping_state IN ('pending', 'active', 'uncertain', 'retired')",
            name="ck_capture_resource_mapping_state",
        ),
        sa.CheckConstraint(
            "(encrypted_external_ref IS NULL) = "
            "(reference_purged_at IS NOT NULL)",
            name="ck_capture_resource_ref_purge",
        ),
        sa.CheckConstraint(
            "(mapping_state = 'retired') = (retired_at IS NOT NULL)",
            name="ck_capture_resource_retired_state",
        ),
        sa.CheckConstraint(
            "role IN ('capture_container', 'recording_media', "
            "'transcript_artifact')",
            name="ck_capture_resource_role",
        ),
        _scope_fk("fk_capture_resource_session_scope"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("capture_external_resource_pkey")),
        sa.UniqueConstraint(
            "adapter_profile",
            "role",
            "external_ref_digest",
            name="uq_capture_resource_adapter_role_digest",
        ),
        comment="Encrypted adapter mapping with keyed-HMAC tombstone.",
    )
    op.create_index(
        "ix_capture_resource_cleanup",
        "capture_external_resource",
        ["cleanup_state", "cleanup_due_at"],
        unique=False,
    )

    op.create_table(
        "capture_launch_authorization",
        _id_column("capture_launch_auth"),
        *_timestamp_columns(),
        *_scope_columns(),
        sa.Column(
            "source_lease_id",
            activemodel.types.typeid.TypeIDType(prefix="capture_source_lease"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="ticket_issued",
            nullable=False,
        ),
        sa.Column(
            "state_version", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("actor_binding_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "installation_thumbprint_digest", sa.String(length=64), nullable=True
        ),
        sa.Column("redirect_uri_digest", sa.String(length=64), nullable=True),
        sa.Column("pkce_code_challenge", sa.String(length=43), nullable=True),
        sa.Column("pkce_method", sa.String(length=8), nullable=True),
        sa.Column(
            "launch_ticket_digest", sa.String(length=64), nullable=False, unique=True
        ),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ticket_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticket_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "authorization_code_digest",
            sa.String(length=64),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "authorization_code_issued_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "authorization_code_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "authorization_code_consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "authorization_code_revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "desktop_token_digest",
            sa.String(length=64),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "desktop_token_issued_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "desktop_token_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "desktop_token_revoked_at", sa.DateTime(timezone=True), nullable=True
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
            "desktop_token_digest IS NULL OR "
            "desktop_token_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_token_digest",
        ),
        sa.CheckConstraint(
            "desktop_token_expires_at IS NULL OR "
            "(desktop_token_expires_at > desktop_token_issued_at AND "
            "desktop_token_expires_at <= "
            "desktop_token_issued_at + INTERVAL '6 hours')",
            name="ck_capture_launch_auth_token_lifetime",
        ),
        sa.CheckConstraint(
            "(redirect_uri_digest IS NULL AND pkce_method IS NULL AND "
            "pkce_code_challenge IS NULL) OR "
            "(redirect_uri_digest IS NOT NULL AND pkce_method = 'S256' AND "
            "pkce_code_challenge ~ '^[A-Za-z0-9_-]{43}$')",
            name="ck_capture_launch_auth_pkce",
        ),
        sa.CheckConstraint(
            "redirect_uri_digest IS NULL OR "
            "redirect_uri_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_redirect_digest",
        ),
        sa.CheckConstraint(
            "ticket_expires_at > created_at AND "
            "ticket_expires_at <= created_at + INTERVAL '2 minutes'",
            name="ck_capture_launch_auth_ticket_lifetime",
        ),
        sa.CheckConstraint(
            "launch_ticket_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_launch_auth_ticket_digest",
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
            "state IN ('ticket_issued', 'code_issued', 'token_issued', "
            "'revoked', 'expired')",
            name="ck_capture_launch_auth_state",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_launch_auth_state_version",
        ),
        _scope_fk("fk_capture_launch_auth_session_scope"),
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
        sa.PrimaryKeyConstraint(
            "id", name=op.f("capture_launch_authorization_pkey")
        ),
        comment=(
            "One-use launch and PKCE exchange. Bearer values are never "
            "persisted; only keyed-HMAC digests remain."
        ),
    )

    op.create_table(
        "capture_event",
        _id_column("capture_event"),
        *_scope_columns(),
        sa.Column(
            "segment_id",
            activemodel.types.typeid.TypeIDType(prefix="capture_segment"),
            nullable=True,
        ),
        sa.Column(
            "operation_id",
            activemodel.types.typeid.TypeIDType(prefix="capture_operation"),
            nullable=True,
        ),
        sa.Column(
            "external_webhook_delivery_id",
            activemodel.types.typeid.TypeIDType(prefix="webhook_delivery"),
            nullable=True,
        ),
        sa.Column("local_sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("session_state", sa.String(length=32), nullable=False),
        sa.Column("source_event_digest", sa.String(length=64), nullable=True),
        sa.Column("provider_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "local_sequence >= 0",
            name="ck_capture_event_local_sequence",
        ),
        sa.CheckConstraint(
            "session_state IN ('awaiting_consent', 'ready', 'starting', "
            "'recording', 'stopping', 'paused', 'processing', "
            "'review_required', 'transcript_ready', 'cancelled', 'failed', "
            "'delivered')",
            name="ck_capture_event_session_state",
        ),
        sa.CheckConstraint(
            "source_event_digest IS NULL OR "
            "source_event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_capture_event_source_digest",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_capture_event_state_version",
        ),
        _scope_fk("fk_capture_event_session_scope"),
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
        sa.ForeignKeyConstraint(
            ["external_webhook_delivery_id"],
            ["external_webhook_delivery.id"],
            name="fk_capture_event_webhook_delivery",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("capture_event_pkey")),
        sa.UniqueConstraint(
            "session_id",
            "local_sequence",
            name="uq_capture_event_session_sequence",
        ),
        comment=(
            "Append-only server-timestamped transition evidence. Provider "
            "timestamps are retained as evidence and never order authority."
        ),
    )
    op.create_index(
        "ix_capture_event_recorded",
        "capture_event",
        ["practice_id", "recorded_at"],
        unique=False,
    )

    op.execute(
        "COMMENT ON TABLE capture_session IS "
        "'One server-authorized or observed capture attempt for an appointment.'"
    )
    op.execute(
        "COMMENT ON TABLE capture_source_lease IS "
        "'Exclusive capture-source lease; expiration alone never closes it.'"
    )
    op.execute(
        "COMMENT ON TABLE capture_segment IS "
        "'One physical capture start; retries allocate a new ordinal and row.'"
    )
    op.execute(
        "COMMENT ON TABLE capture_operation IS "
        "'Durable idempotency and ambiguous-outcome fence for capture actions.'"
    )
    op.execute(
        "COMMENT ON TABLE capture_external_resource IS "
        "'Encrypted adapter mapping with a durable keyed-HMAC tombstone.'"
    )
    op.execute(
        "COMMENT ON TABLE capture_launch_authorization IS "
        "'One-use launch and PKCE exchange; bearer secrets are never persisted.'"
    )
    op.execute(
        "COMMENT ON TABLE capture_event IS "
        "'Append-only, server-timestamped capture transition evidence.'"
    )

    op.execute(
        """
        CREATE FUNCTION capture_event_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                NEW.recorded_at := clock_timestamp();
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'capture_event is append-only'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER capture_event_append_only
        BEFORE INSERT OR UPDATE OR DELETE ON capture_event
        FOR EACH ROW EXECUTE FUNCTION capture_event_reject_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION capture_segment_guard_physical_start()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.physical_started_at IS NOT NULL
               AND NEW.physical_started_at IS DISTINCT FROM OLD.physical_started_at
            THEN
                RAISE EXCEPTION 'capture segment physical start is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.capability_id IS NOT NULL
               AND (NEW.capability_id IS DISTINCT FROM OLD.capability_id
                    OR NEW.capability_digest IS DISTINCT FROM OLD.capability_digest
                    OR NEW.capability_issued_at IS DISTINCT FROM
                        OLD.capability_issued_at
                    OR NEW.capability_expires_at IS DISTINCT FROM
                        OLD.capability_expires_at)
            THEN
                RAISE EXCEPTION 'capture segment capability identity is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.capability_consumed_at IS NOT NULL
               AND NEW.capability_consumed_at IS DISTINCT FROM
                   OLD.capability_consumed_at
            THEN
                RAISE EXCEPTION 'capture capability consumption is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.capability_revoked_at IS NOT NULL
               AND NEW.capability_revoked_at IS DISTINCT FROM
                   OLD.capability_revoked_at
            THEN
                RAISE EXCEPTION 'capture capability revocation is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.capability_purged_at IS NOT NULL
               AND NEW.capability_purged_at IS DISTINCT FROM
                   OLD.capability_purged_at
            THEN
                RAISE EXCEPTION 'capture capability purge evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER capture_segment_one_physical_start
        BEFORE UPDATE ON capture_segment
        FOR EACH ROW EXECUTE FUNCTION capture_segment_guard_physical_start()
        """
    )
    op.execute(
        """
        CREATE FUNCTION capture_operation_guard_secret_purge()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.replay_response_purged_at IS NOT NULL
               AND NEW.replay_response_purged_at IS DISTINCT FROM
                   OLD.replay_response_purged_at
            THEN
                RAISE EXCEPTION 'capture replay purge evidence is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER capture_operation_secret_purge
        BEFORE UPDATE ON capture_operation
        FOR EACH ROW EXECUTE FUNCTION capture_operation_guard_secret_purge()
        """
    )
    op.execute(
        """
        CREATE FUNCTION capture_launch_auth_guard_one_use()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.source_lease_id IS DISTINCT FROM OLD.source_lease_id
               OR NEW.actor_binding_digest IS DISTINCT FROM
                   OLD.actor_binding_digest
               OR NEW.launch_ticket_digest IS DISTINCT FROM
                   OLD.launch_ticket_digest
               OR NEW.ticket_expires_at IS DISTINCT FROM OLD.ticket_expires_at
            THEN
                RAISE EXCEPTION 'launch authorization binding is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.ticket_consumed_at IS NOT NULL
               AND NEW.ticket_consumed_at IS DISTINCT FROM OLD.ticket_consumed_at
            THEN
                RAISE EXCEPTION 'launch ticket consumption is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.authorization_code_digest IS NOT NULL
               AND (NEW.authorization_code_digest IS DISTINCT FROM
                        OLD.authorization_code_digest
                    OR NEW.authorization_code_issued_at IS DISTINCT FROM
                        OLD.authorization_code_issued_at
                    OR NEW.authorization_code_expires_at IS DISTINCT FROM
                        OLD.authorization_code_expires_at
                    OR NEW.redirect_uri_digest IS DISTINCT FROM
                        OLD.redirect_uri_digest
                    OR NEW.pkce_code_challenge IS DISTINCT FROM
                        OLD.pkce_code_challenge
                    OR NEW.pkce_method IS DISTINCT FROM OLD.pkce_method
                    OR NEW.installation_thumbprint_digest IS DISTINCT FROM
                        OLD.installation_thumbprint_digest)
            THEN
                RAISE EXCEPTION 'authorization code binding is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.ticket_revoked_at IS NOT NULL
               AND NEW.ticket_revoked_at IS DISTINCT FROM OLD.ticket_revoked_at
            THEN
                RAISE EXCEPTION 'launch ticket revocation is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.desktop_token_digest IS NOT NULL
               AND (NEW.desktop_token_digest IS DISTINCT FROM
                        OLD.desktop_token_digest
                    OR NEW.desktop_token_issued_at IS DISTINCT FROM
                        OLD.desktop_token_issued_at
                    OR NEW.desktop_token_expires_at IS DISTINCT FROM
                        OLD.desktop_token_expires_at)
            THEN
                RAISE EXCEPTION 'desktop token binding is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.authorization_code_consumed_at IS NOT NULL
               AND NEW.authorization_code_consumed_at IS DISTINCT FROM
                   OLD.authorization_code_consumed_at
            THEN
                RAISE EXCEPTION 'authorization code consumption is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.authorization_code_revoked_at IS NOT NULL
               AND NEW.authorization_code_revoked_at IS DISTINCT FROM
                   OLD.authorization_code_revoked_at
            THEN
                RAISE EXCEPTION 'authorization code revocation is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF OLD.desktop_token_revoked_at IS NOT NULL
               AND NEW.desktop_token_revoked_at IS DISTINCT FROM
                   OLD.desktop_token_revoked_at
            THEN
                RAISE EXCEPTION 'desktop token revocation is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER capture_launch_auth_one_use
        BEFORE UPDATE ON capture_launch_authorization
        FOR EACH ROW EXECUTE FUNCTION capture_launch_auth_guard_one_use()
        """
    )


def downgrade() -> None:
    """Remove the pre-activation schema only.

    Once capture accepts traffic, application/flag rollback must preserve
    sessions, leases, operations, authorizations, and append-only events for
    reconciliation and forensic review. Disable ingress, drain workers, and
    preserve or back up the evidence before an explicitly approved downgrade.
    """

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM capture_event LIMIT 1)
               OR EXISTS (SELECT 1 FROM capture_launch_authorization LIMIT 1)
               OR EXISTS (SELECT 1 FROM capture_external_resource LIMIT 1)
               OR EXISTS (SELECT 1 FROM capture_operation LIMIT 1)
               OR EXISTS (SELECT 1 FROM capture_segment LIMIT 1)
               OR EXISTS (SELECT 1 FROM capture_source_lease LIMIT 1)
               OR EXISTS (SELECT 1 FROM capture_session LIMIT 1)
               OR EXISTS (
                    SELECT 1 FROM appointment
                    WHERE cancelled_at IS NOT NULL
                    LIMIT 1
               )
            THEN
                RAISE EXCEPTION
                    'capture coordination downgrade requires empty evidence'
                    USING ERRCODE = 'object_not_in_prerequisite_state';
            END IF;
        END;
        $$
        """
    )

    op.execute(
        "DROP TRIGGER capture_launch_auth_one_use "
        "ON capture_launch_authorization"
    )
    op.execute("DROP FUNCTION capture_launch_auth_guard_one_use()")
    op.execute(
        "DROP TRIGGER capture_operation_secret_purge ON capture_operation"
    )
    op.execute("DROP FUNCTION capture_operation_guard_secret_purge()")
    op.execute(
        "DROP TRIGGER capture_segment_one_physical_start ON capture_segment"
    )
    op.execute("DROP FUNCTION capture_segment_guard_physical_start()")
    op.execute("DROP TRIGGER capture_event_append_only ON capture_event")
    op.execute("DROP FUNCTION capture_event_reject_mutation()")

    op.drop_index("ix_capture_event_recorded", table_name="capture_event")
    op.drop_table("capture_event")
    op.drop_table("capture_launch_authorization")
    op.drop_index(
        "ix_capture_resource_cleanup", table_name="capture_external_resource"
    )
    op.drop_table("capture_external_resource")
    op.drop_index(
        "ix_capture_operation_recovery", table_name="capture_operation"
    )
    op.drop_table("capture_operation")
    op.drop_table("capture_segment")
    op.drop_index(
        "uq_capture_source_lease_open_appointment",
        table_name="capture_source_lease",
    )
    op.drop_index(
        "ix_capture_source_lease_control_deadline",
        table_name="capture_source_lease",
    )
    op.drop_table("capture_source_lease")
    op.drop_index(
        "ix_capture_session_appointment_created", table_name="capture_session"
    )
    op.drop_table("capture_session")

    op.drop_constraint(
        "uq_practice_member_practice_doctor_capture",
        "practice_member",
        type_="unique",
    )
    op.drop_constraint(
        "uq_appointment_id_doctor_id_capture",
        "appointment",
        type_="unique",
    )
    op.drop_column("appointment", "cancelled_at")
