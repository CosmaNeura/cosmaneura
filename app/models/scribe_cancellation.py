"""Self-only cancellation fence; no shared-login or clinical-data mutation."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel

# This ledger deliberately holds no token, clinical text, blob path, or patient
# identity. Existing Doctor/Clerk identifiers are never deleted or reparented.
scribe_cancellation_operation = sa.Table(
    "scribe_cancellation_operation",
    SQLModel.metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("caller_sha256", sa.String(64), nullable=False),
    sa.Column("doctor_ref", sa.String(100), nullable=False),
    sa.Column("request_key_sha256", sa.String(64), nullable=False),
    sa.Column("preview_sha256", sa.String(64), nullable=False),
    sa.Column("inventory_sha256", sa.String(64), nullable=False),
    sa.Column("inventory", JSONB().with_variant(sa.JSON, "sqlite"), nullable=False),
    sa.Column("policy", JSONB().with_variant(sa.JSON, "sqlite"), nullable=False),
    sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("export_until", sa.DateTime(timezone=True), nullable=False),
    sa.Column("phase", sa.String(16), nullable=False),
    sa.UniqueConstraint("caller_sha256", name="uq_scribe_cancellation_caller"),
    sa.UniqueConstraint("doctor_ref", name="uq_scribe_cancellation_doctor"),
    sa.CheckConstraint("phase = 'read-only'", name="ck_scribe_cancellation_read_only"),
    sa.CheckConstraint(
        "caller_sha256 ~ '^[a-f0-9]{64}$' AND request_key_sha256 ~ '^[a-f0-9]{64}$' AND "
        "preview_sha256 ~ '^[a-f0-9]{64}$' AND inventory_sha256 ~ '^[a-f0-9]{64}$'",
        name="ck_scribe_cancellation_digests",
    ).ddl_if(dialect="postgresql"),
    sa.CheckConstraint(
        "export_until >= accepted_at + interval '30 days' AND "
        "export_until <= accepted_at + interval '90 days'",
        name="ck_scribe_cancellation_window",
    ).ddl_if(dialect="postgresql"),
    sa.CheckConstraint(
        "jsonb_typeof(inventory) = 'array' AND jsonb_typeof(policy) = 'object' AND "
        "policy ?& ARRAY['grace_days', 'phi_scope', 'approval_sha256'] AND "
        "policy - ARRAY['grace_days', 'phi_scope', 'approval_sha256'] = '{}'::jsonb AND "
        "jsonb_typeof(policy->'grace_days') = 'number' AND "
        "(policy->>'grace_days')::int BETWEEN 30 AND 90 AND "
        "policy->>'phi_scope' IN ('scribe-processing-copies', 'core-and-scribe-clinical-records') AND "
        "policy->>'approval_sha256' ~ '^[a-f0-9]{64}$' AND "
        "export_until = accepted_at + (policy->>'grace_days')::int * interval '1 day'",
        name="ck_scribe_cancellation_policy",
    ).ddl_if(dialect="postgresql"),
    comment="Immutable self-only Scribe cancellation receipt; not an erasure manifest.",
)
