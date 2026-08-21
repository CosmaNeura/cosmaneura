"""Durable idempotency receipts for Recall webhook side effects."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class ExternalWebhookOperation(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("webhook_operation"),
    table=True,
):
    """Provider-resource and downstream-publication fence.

    Only a SHA-256 digest of the adapter-private provider resource reference is
    stored.  An ``in_flight`` create-transcript receipt is intentionally not
    replayed after an ambiguous failure; a later signed transcript callback
    reconciles it to ``succeeded``.
    """

    __table_args__ = (
        sa.UniqueConstraint(
            "provider",
            "operation_type",
            "provider_resource_digest",
            name="uq_external_webhook_operation_resource",
        ),
        sa.CheckConstraint(
            "provider = 'recall'",
            name="ck_external_webhook_operation_provider",
        ),
        sa.CheckConstraint(
            "operation_type IN ('create_transcript', 'ingest_transcript')",
            name="ck_external_webhook_operation_type",
        ),
        sa.CheckConstraint(
            "state IN ('in_flight', 'succeeded', 'uncertain')",
            name="ck_external_webhook_operation_state",
        ),
        sa.CheckConstraint(
            "provider_resource_digest ~ '^[0-9a-f]{64}$'",
            name="ck_external_webhook_operation_resource_digest",
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_external_webhook_operation_content_digest",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('provider_outcome_uncertain', 'pipeline_publish_failed')",
            name="ck_external_webhook_operation_error_code",
        ),
        sa.CheckConstraint(
            "pipeline_attempt_count >= 0",
            name="ck_external_webhook_operation_pipeline_attempt_count",
        ),
        sa.Index(
            "ix_external_webhook_operation_pipeline_due",
            "operation_type",
            "state",
            "pipeline_lease_expires_at",
        ),
    )

    provider: str = Field(default="recall", max_length=16)
    operation_type: str = Field(max_length=32)
    provider_resource_digest: str = Field(max_length=64)
    state: str = Field(default="in_flight", max_length=16)
    content_sha256: str | None = Field(default=None, max_length=64)
    result_transcript_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("ai_visit_transcript"), nullable=True),
    )
    schema_pipeline_dispatched_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    engagement_pipeline_dispatched_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    pipeline_dispatched_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    pipeline_lease_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    pipeline_attempt_count: int = 0
    last_error_code: str | None = Field(default=None, max_length=48)
