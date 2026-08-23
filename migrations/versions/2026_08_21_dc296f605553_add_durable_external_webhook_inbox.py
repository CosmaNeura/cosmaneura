"""add durable external webhook inbox

Revision ID: dc296f605553
Revises: ab25569de9c6
Create Date: 2026-08-21 10:00:00.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "dc296f605553"
down_revision: Union[str, None] = "ab25569de9c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_webhook_delivery",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="webhook_delivery"),
            nullable=False,
            comment="TypeID with prefix: webhook_delivery",
        ),
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
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provider_event_digest", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column(
            "encrypted_payload",
            sa.Text(),
            nullable=True,
        ),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=24),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "attempt_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wakeup_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=48), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_external_webhook_delivery_attempt_count"
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('decrypt_failed', 'handler_error', 'invalid_payload', "
            "'meeting_mapping_unavailable', 'payload_expired', "
            "'provider_operation_uncertain', 'provider_transcript_failed', "
            "'tenant_mapping_unavailable')",
            name="ck_external_webhook_delivery_error_code",
        ),
        sa.CheckConstraint(
            "provider_event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_external_webhook_delivery_event_digest",
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_external_webhook_delivery_payload_digest",
        ),
        sa.CheckConstraint(
            "provider IN ('recall', 'zoom')",
            name="ck_external_webhook_delivery_provider",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'retry_wait', 'completed', 'dead_letter')",
            name="ck_external_webhook_delivery_state",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("external_webhook_delivery_pkey")),
        sa.UniqueConstraint(
            "provider",
            "provider_event_digest",
            name="uq_external_webhook_delivery_provider_event",
        ),
        comment=(
            "Durable receipt written before a provider webhook is acknowledged.\n\n"
            "Provider delivery identifiers are irreversibly digested.  The minimum\n"
            "payload needed by the adapter is encrypted and is purged on success or at\n"
            "the six-hour expiry; headers, signatures and callback URLs are never\n"
            "persisted."
        ),
    )
    op.create_index(
        "ix_external_webhook_delivery_due",
        "external_webhook_delivery",
        ["state", "next_attempt_at", "wakeup_after"],
        unique=False,
    )
    op.create_index(
        "ix_external_webhook_delivery_lease",
        "external_webhook_delivery",
        ["state", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_external_webhook_delivery_payload_expiry",
        "external_webhook_delivery",
        ["payload_expires_at"],
        unique=False,
    )

    op.create_table(
        "external_webhook_operation",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="webhook_operation"),
            nullable=False,
            comment="TypeID with prefix: webhook_operation",
        ),
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
        sa.Column(
            "provider", sa.String(length=16), server_default="recall", nullable=False
        ),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("provider_resource_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="in_flight",
            nullable=False,
        ),
        sa.Column(
            "execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "execution_attempt_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "result_transcript_id",
            activemodel.types.typeid.TypeIDType(prefix="ai_visit_transcript"),
            nullable=True,
        ),
        sa.Column(
            "schema_pipeline_dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "engagement_pipeline_dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("pipeline_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "pipeline_lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "pipeline_attempt_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_error_code", sa.String(length=48), nullable=True),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_external_webhook_operation_content_digest",
        ),
        sa.CheckConstraint(
            "execution_attempt_count >= 0",
            name="ck_external_webhook_operation_execution_attempt_count",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('provider_outcome_uncertain', 'provider_transcript_failed', "
            "'pipeline_publish_failed')",
            name="ck_external_webhook_operation_error_code",
        ),
        sa.CheckConstraint(
            "pipeline_attempt_count >= 0",
            name="ck_external_webhook_operation_pipeline_attempt_count",
        ),
        sa.CheckConstraint(
            "operation_type IN ('create_transcript', 'ingest_transcript', "
            "'zoom_meeting_started', 'zoom_participant_joined', "
            "'zoom_participant_left', 'zoom_meeting_ended')",
            name="ck_external_webhook_operation_type",
        ),
        sa.CheckConstraint(
            "provider IN ('recall', 'zoom')",
            name="ck_external_webhook_operation_provider",
        ),
        sa.CheckConstraint(
            "provider_resource_digest ~ '^[0-9a-f]{64}$'",
            name="ck_external_webhook_operation_resource_digest",
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'in_flight', 'succeeded', 'uncertain', 'failed')",
            name="ck_external_webhook_operation_state",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("external_webhook_operation_pkey")),
        sa.UniqueConstraint(
            "provider",
            "operation_type",
            "provider_resource_digest",
            name="uq_external_webhook_operation_resource",
        ),
        comment=(
            "Provider-resource and downstream-publication fence.\n\n"
            "Only a context-separated HMAC-SHA-256 pseudonym of the adapter-private\n"
            "provider resource reference is stored. An ``in_flight`` create-transcript\n"
            "receipt is intentionally not\n"
            "replayed after an ambiguous failure; a later signed transcript callback\n"
            "reconciles it to ``succeeded``."
        ),
    )
    op.create_index(
        "ix_external_webhook_operation_execution_lease",
        "external_webhook_operation",
        ["provider", "state", "execution_lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_external_webhook_operation_pipeline_due",
        "external_webhook_operation",
        ["operation_type", "state", "pipeline_lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_webhook_operation_execution_lease",
        table_name="external_webhook_operation",
    )
    op.drop_index(
        "ix_external_webhook_operation_pipeline_due",
        table_name="external_webhook_operation",
    )
    op.drop_table("external_webhook_operation")
    op.drop_index(
        "ix_external_webhook_delivery_payload_expiry",
        table_name="external_webhook_delivery",
    )
    op.drop_index(
        "ix_external_webhook_delivery_lease",
        table_name="external_webhook_delivery",
    )
    op.drop_index(
        "ix_external_webhook_delivery_due",
        table_name="external_webhook_delivery",
    )
    op.drop_table("external_webhook_delivery")
