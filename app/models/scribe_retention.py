"""Additive non-PHI lifecycle evidence, independent of immutable intake rows."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel

scribe_retention_event = sa.Table(
    "scribe_retention_event",
    SQLModel.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "operation_id",
        sa.Uuid,
        sa.ForeignKey("scribe_cancellation_operation.id"),
        nullable=False,
    ),
    sa.Column("sequence", sa.Integer, nullable=False),
    sa.Column("event_key_sha256", sa.String(64), nullable=False),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("payload", JSONB().with_variant(sa.JSON, "sqlite"), nullable=False),
    sa.UniqueConstraint(
        "operation_id", "sequence", name="uq_scribe_retention_sequence"
    ),
    sa.UniqueConstraint(
        "operation_id", "event_key_sha256", name="uq_scribe_retention_retry"
    ),
    sa.CheckConstraint("sequence > 0", name="ck_scribe_retention_sequence"),
    sa.CheckConstraint(
        "kind IN ('expiry-hold', 'erasure-intent', 'verified-receipt', 'certificate-ready', 'certificate-issued')",
        name="ck_scribe_retention_kind",
    ),
    sa.CheckConstraint(
        "event_key_sha256 ~ '^[a-f0-9]{64}$' AND jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536",
        name="ck_scribe_retention_payload",
    ).ddl_if(dialect="postgresql"),
    comment="Append-only non-PHI erasure lifecycle evidence; no PHI, blob paths, tokens or login mutation.",
)
