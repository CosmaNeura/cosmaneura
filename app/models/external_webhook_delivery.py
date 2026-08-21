"""Encrypted, short-lived ingress records for signed provider webhooks."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlmodel import Column, Field

WEBHOOK_PROVIDERS = ("recall", "zoom")
WEBHOOK_DELIVERY_STATES = (
    "pending",
    "processing",
    "retry_wait",
    "completed",
    "dead_letter",
)
WEBHOOK_ERROR_CODES = (
    "decrypt_failed",
    "handler_error",
    "invalid_payload",
    "meeting_mapping_unavailable",
    "payload_expired",
    "provider_operation_uncertain",
    "provider_transcript_failed",
    "tenant_mapping_unavailable",
)


class ExternalWebhookDelivery(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("webhook_delivery"),
    table=True,
):
    """Durable receipt written before a provider webhook is acknowledged.

    Provider delivery identifiers are irreversibly digested.  The minimum
    payload needed by the adapter is encrypted and is purged on success or at
    the six-hour expiry; headers, signatures and callback URLs are never
    persisted.
    """

    __table_args__ = (
        sa.UniqueConstraint(
            "provider",
            "provider_event_digest",
            name="uq_external_webhook_delivery_provider_event",
        ),
        sa.CheckConstraint(
            "provider IN ('recall', 'zoom')",
            name="ck_external_webhook_delivery_provider",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'retry_wait', 'completed', 'dead_letter')",
            name="ck_external_webhook_delivery_state",
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
            "last_error_code IS NULL OR last_error_code IN "
            "('decrypt_failed', 'handler_error', 'invalid_payload', "
            "'meeting_mapping_unavailable', 'payload_expired', "
            "'provider_operation_uncertain', 'provider_transcript_failed', "
            "'tenant_mapping_unavailable')",
            name="ck_external_webhook_delivery_error_code",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_external_webhook_delivery_attempt_count",
        ),
        sa.Index(
            "ix_external_webhook_delivery_due",
            "state",
            "next_attempt_at",
            "wakeup_after",
        ),
        sa.Index(
            "ix_external_webhook_delivery_lease",
            "state",
            "lease_expires_at",
        ),
        sa.Index(
            "ix_external_webhook_delivery_payload_expiry",
            "payload_expires_at",
        ),
    )

    provider: str = Field(max_length=16)
    provider_event_digest: str = Field(max_length=64)
    event_type: str = Field(max_length=96)
    encrypted_payload: str | None = Field(
        default=None,
        # Inbox decryption is deliberately explicit in the claim transaction.
        # A TypeDecorator would raise while SQLAlchemy materialises this row,
        # before the worker can quarantine corrupt or retired-key ciphertext.
        sa_column=Column(sa.Text(), nullable=True),
    )
    payload_sha256: str = Field(max_length=64)
    state: str = Field(default="pending", max_length=24)
    attempt_count: int = 0
    next_attempt_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    wakeup_after: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    received_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    verified_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    processed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    payload_expires_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    payload_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    last_error_code: str | None = Field(default=None, max_length=48)
