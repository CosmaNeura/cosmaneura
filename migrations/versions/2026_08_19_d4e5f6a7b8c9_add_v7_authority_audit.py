"""add provider-first V7 authority and observational audit foundation

Revision ID: d4e5f6a7b8c9
Revises: 3a7b9c0d1e2f
Create Date: 2026-08-19 12:00:00.000000
"""

from collections.abc import Sequence

import activemodel
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "3a7b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DURABLE_V7_TABLES = (
    "authority_tenant",
    "authority_actor",
    "authority_person",
    "authority_actor_external_identity",
    "authority_tenant_membership",
    "authority_person_origin",
    "authority_person_binding",
    "mount_context_session",
    "mount_context_token",
    "interaction_lineage",
    "authority_idempotency_record",
    "agent_model_usage_event",
    "canonical_conversation_turn",
    "canonical_conversation_user_phase",
    "canonical_conversation_final_phase",
    "authority_audit_event",
)


def _id(prefix: str) -> sa.Column:
    return sa.Column(
        "id",
        activemodel.types.typeid.TypeIDType(prefix=prefix),
        nullable=False,
        comment=f"TypeID with prefix: {prefix}",
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
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


def _refuse_populated_or_offline_downgrade() -> None:
    """Never erase durable V7 authority facts as an ordinary rollback."""

    if op.get_context().as_sql:
        raise RuntimeError("V7 authority downgrade is unsupported in offline mode")
    connection = op.get_bind()
    # The migration transaction retains these locks through the checks and all
    # following DROP statements. A writer that committed first is observed;
    # one that arrives later cannot slip a row between EXISTS and DROP.
    for table_name in _DURABLE_V7_TABLES:
        connection.execute(
            sa.text(f'LOCK TABLE "{table_name}" IN ACCESS EXCLUSIVE MODE')
        )
    populated = [
        table_name
        for table_name in _DURABLE_V7_TABLES
        if connection.execute(
            sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table_name}" LIMIT 1)')
        ).scalar()
    ]
    if populated:
        raise RuntimeError(
            "V7 authority downgrade refused because durable records exist in: "
            + ", ".join(populated)
        )


def upgrade() -> None:
    op.create_table(
        "authority_tenant",
        _id("authority_tenant"),
        *_timestamps(),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_authority_tenant_status",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("authority_tenant_pkey")),
        comment="A practice or other platform tenancy boundary.",
    )
    op.create_index(
        op.f("authority_tenant_status_idx"),
        "authority_tenant",
        ["status"],
        unique=False,
    )

    op.create_table(
        "authority_actor",
        _id("authority_actor"),
        *_timestamps(),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "actor_kind IN ('human', 'workload')",
            name="ck_authority_actor_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_authority_actor_status",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("authority_actor_pkey")),
        comment="A platform principal; never inferred from request-body identity hints.",
    )
    op.create_index(
        op.f("authority_actor_status_idx"),
        "authority_actor",
        ["status"],
        unique=False,
    )

    op.create_table(
        "authority_person",
        _id("authority_person"),
        *_timestamps(),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "merged_into_person_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_person"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('prospect', 'patient', 'enrolled')",
            name="ck_authority_person_lifecycle_state",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'merged', 'revoked')",
            name="ck_authority_person_status",
        ),
        sa.CheckConstraint(
            "(status = 'merged' AND merged_into_person_id IS NOT NULL) OR "
            "(status <> 'merged' AND merged_into_person_id IS NULL)",
            name="ck_authority_person_merge_target",
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_person_id"],
            ["authority_person.id"],
            name=op.f("authority_person_merged_into_person_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("authority_person_pkey")),
        comment="Canonical person lifecycle without demographics or contact data.",
    )
    op.create_index(
        op.f("authority_person_lifecycle_state_idx"),
        "authority_person",
        ["lifecycle_state"],
        unique=False,
    )
    op.create_index(
        op.f("authority_person_status_idx"),
        "authority_person",
        ["status"],
        unique=False,
    )

    op.create_table(
        "authority_actor_external_identity",
        _id("actor_identity"),
        *_timestamps(),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=False,
        ),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("authority_actor_external_identity_actor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("authority_actor_external_identity_pkey"),
        ),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_authority_actor_external_identity_issuer_subject",
        ),
        comment="A verified IdP subject bound to exactly one platform actor.",
    )

    op.create_table(
        "authority_tenant_membership",
        _id("tenant_membership"),
        *_timestamps(),
        sa.Column(
            "tenant_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_tenant"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=False,
        ),
        sa.Column("membership_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "membership_kind IN ('provider', 'staff')",
            name="ck_authority_tenant_membership_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_authority_tenant_membership_status",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("authority_tenant_membership_actor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["authority_tenant.id"],
            name=op.f("authority_tenant_membership_tenant_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("authority_tenant_membership_pkey"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_id",
            name="uq_authority_tenant_membership_tenant_actor",
        ),
        comment="Current actor membership used by server policy, not a client role claim.",
    )
    op.create_index(
        op.f("authority_tenant_membership_status_idx"),
        "authority_tenant_membership",
        ["status"],
        unique=False,
    )

    op.create_table(
        "authority_person_origin",
        _id("person_origin"),
        *_timestamps(),
        sa.Column(
            "person_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_person"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=160), nullable=True),
        sa.CheckConstraint(
            "origin IN ('sync', 'provider_entry', 'self_service')",
            name="ck_authority_person_origin",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["authority_person.id"],
            name=op.f("authority_person_origin_person_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("authority_person_origin_pkey"),
        ),
        comment="Append-only provenance retained when canonical person records are merged.",
    )
    op.create_index(
        op.f("authority_person_origin_origin_idx"),
        "authority_person_origin",
        ["origin"],
        unique=False,
    )

    op.create_table(
        "authority_person_binding",
        _id("person_binding"),
        *_timestamps(),
        sa.Column(
            "person_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_person"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_tenant"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=True,
        ),
        sa.Column("binding_ref", sa.String(length=160), nullable=False),
        sa.Column("binding_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "binding_kind IN ('claim_link', 'prospect_session', "
            "'identified_session', 'provider_record')",
            name="ck_authority_person_binding_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_authority_person_binding_status",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("authority_person_binding_actor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["authority_person.id"],
            name=op.f("authority_person_binding_person_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["authority_tenant.id"],
            name=op.f("authority_person_binding_tenant_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("authority_person_binding_pkey"),
        ),
        comment="Opaque, verified link between a person and an identity or tenant record.",
    )
    op.create_index(
        op.f("authority_person_binding_binding_ref_idx"),
        "authority_person_binding",
        ["binding_ref"],
        unique=True,
    )
    op.create_index(
        op.f("authority_person_binding_status_idx"),
        "authority_person_binding",
        ["status"],
        unique=False,
    )

    op.create_table(
        "mount_context_session",
        _id("mount_session"),
        *_timestamps(),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_tenant"),
            nullable=True,
        ),
        sa.Column(
            "person_binding_id",
            activemodel.types.typeid.TypeIDType(prefix="person_binding"),
            nullable=True,
        ),
        sa.Column(
            "source_identity_id",
            activemodel.types.typeid.TypeIDType(prefix="actor_identity"),
            nullable=True,
        ),
        sa.Column("session_kind", sa.String(length=32), nullable=False),
        sa.Column("surface_ceiling", sa.String(length=32), nullable=False),
        sa.Column("auth_tier_ceiling", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "absolute_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "session_kind IN ('anonymous', 'authenticated')",
            name="ck_mount_context_session_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_mount_context_session_status",
        ),
        sa.CheckConstraint(
            "surface_ceiling IN ('public', 'provider_assistant')",
            name="ck_mount_context_session_surface_ceiling",
        ),
        sa.CheckConstraint(
            "auth_tier_ceiling IN "
            "('anonymous', 'prospect', 'identified', 'enrolled', 'provider')",
            name="ck_mount_context_session_auth_tier_ceiling",
        ),
        sa.CheckConstraint(
            "(session_kind = 'anonymous' AND actor_id IS NULL "
            "AND tenant_id IS NULL AND person_binding_id IS NULL "
            "AND source_identity_id IS NULL "
            "AND surface_ceiling = 'public' "
            "AND auth_tier_ceiling = 'anonymous') OR "
            "(session_kind = 'authenticated' AND actor_id IS NOT NULL "
            "AND person_binding_id IS NOT NULL "
            "AND source_identity_id IS NOT NULL)",
            name="ck_mount_context_session_binding_shape",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("mount_context_session_actor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["person_binding_id"],
            ["authority_person_binding.id"],
            name=op.f("mount_context_session_person_binding_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["source_identity_id"],
            ["authority_actor_external_identity.id"],
            name=op.f("mount_context_session_source_identity_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["authority_tenant.id"],
            name=op.f("mount_context_session_tenant_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("mount_context_session_pkey"),
        ),
        comment="Server-side binding behind one family of opaque mount-context tokens.",
    )
    op.create_index(
        op.f("mount_context_session_absolute_expires_at_idx"),
        "mount_context_session",
        ["absolute_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("mount_context_session_status_idx"),
        "mount_context_session",
        ["status"],
        unique=False,
    )

    op.create_table(
        "mount_context_token",
        _id("mount_token"),
        sa.Column(
            "session_id",
            activemodel.types.typeid.TypeIDType(prefix="mount_session"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["mount_context_session.id"],
            name=op.f("mount_context_token_session_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("mount_context_token_pkey")),
        comment="Hashed bearer-token receipt. Token plaintext is never persisted.",
    )
    op.create_index(
        op.f("mount_context_token_expires_at_idx"),
        "mount_context_token",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("mount_context_token_token_hash_idx"),
        "mount_context_token",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "interaction_lineage",
        _id("interaction"),
        *_timestamps(),
        sa.Column("workload_client_id", sa.String(length=64), nullable=False),
        sa.Column("client_turn_id", sa.Uuid(), nullable=False),
        sa.Column(
            "session_id",
            activemodel.types.typeid.TypeIDType(prefix="mount_session"),
            nullable=False,
        ),
        sa.Column("interaction_ref", sa.String(length=160), nullable=False),
        sa.Column("binding_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("audit_status", sa.String(length=16), nullable=False),
        sa.Column("usage_status", sa.String(length=16), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "audit_status IN ('pending', 'complete', 'failed')",
            name="ck_interaction_lineage_audit_status",
        ),
        sa.CheckConstraint(
            "usage_status IN ('pending', 'complete', 'failed')",
            name="ck_interaction_lineage_usage_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["mount_context_session.id"],
            name=op.f("interaction_lineage_session_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("interaction_lineage_pkey")),
        comment="Non-authorizing correlation for exactly one logical client turn.",
    )
    op.create_index(
        op.f("interaction_lineage_audit_status_idx"),
        "interaction_lineage",
        ["audit_status"],
        unique=False,
    )
    op.create_index(
        op.f("interaction_lineage_client_turn_id_idx"),
        "interaction_lineage",
        ["client_turn_id"],
        unique=True,
    )
    op.create_index(
        op.f("interaction_lineage_interaction_ref_idx"),
        "interaction_lineage",
        ["interaction_ref"],
        unique=True,
    )
    op.create_index(
        op.f("interaction_lineage_usage_status_idx"),
        "interaction_lineage",
        ["usage_status"],
        unique=False,
    )
    op.create_index(
        op.f("interaction_lineage_workload_client_id_idx"),
        "interaction_lineage",
        ["workload_client_id"],
        unique=False,
    )

    op.create_table(
        "authority_idempotency_record",
        _id("authority_idem"),
        *_timestamps(),
        sa.Column("workload_client_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=96), nullable=False),
        sa.Column(
            "interaction_id",
            activemodel.types.typeid.TypeIDType(prefix="interaction"),
            nullable=False,
        ),
        sa.Column("logical_ref", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_ref", sa.String(length=160), nullable=True),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'failed')",
            name="ck_authority_idempotency_status",
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interaction_lineage.id"],
            name=op.f("authority_idempotency_record_interaction_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("authority_idempotency_record_pkey"),
        ),
        sa.UniqueConstraint(
            "workload_client_id",
            "operation",
            "interaction_id",
            "idempotency_key",
            name="uq_authority_idempotency_scope_key",
        ),
        sa.UniqueConstraint(
            "workload_client_id",
            "operation",
            "interaction_id",
            "logical_ref",
            name="uq_authority_idempotency_logical_operation",
        ),
        comment="Twenty-four-hour receipt binding for Agent API writes.",
    )
    op.create_index(
        op.f("authority_idempotency_record_retain_until_idx"),
        "authority_idempotency_record",
        ["retain_until"],
        unique=False,
    )
    op.create_index(
        op.f("authority_idempotency_record_status_idx"),
        "authority_idempotency_record",
        ["status"],
        unique=False,
    )

    op.create_table(
        "agent_model_usage_event",
        _id("model_usage"),
        sa.Column(
            "interaction_id",
            activemodel.types.typeid.TypeIDType(prefix="interaction"),
            nullable=False,
        ),
        sa.Column("workload_client_id", sa.String(length=64), nullable=False),
        sa.Column("client_event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tenant_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_tenant"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_person"),
            nullable=False,
        ),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("auth_tier", sa.String(length=16), nullable=False),
        sa.Column("call_kind", sa.String(length=32), nullable=False),
        sa.Column("call_sequence", sa.BigInteger(), nullable=False),
        sa.Column("workstream", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("deployment", sa.String(length=200), nullable=True),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("usage_availability", sa.String(length=16), nullable=False),
        sa.Column("usage_source", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("agent_model_usage_event_actor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interaction_lineage.id"],
            name=op.f("agent_model_usage_event_interaction_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["authority_person.id"],
            name=op.f("agent_model_usage_event_person_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["authority_tenant.id"],
            name=op.f("agent_model_usage_event_tenant_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("agent_model_usage_event_pkey")),
        sa.UniqueConstraint(
            "workload_client_id",
            "interaction_id",
            "client_event_id",
            name="uq_agent_model_usage_event_logical",
        ),
        sa.UniqueConstraint(
            "interaction_id",
            "call_sequence",
            name="uq_agent_model_usage_event_call_sequence",
        ),
        comment="Append-only provider-reported model call without content or PHI.",
    )
    for constraint_name, condition in (
        (
            "ck_agent_model_usage_event_call_kind",
            "call_kind IN ('classification', 'routing', 'reasoning', 'drafting', "
            "'doctrinal_arbitration', 'tool_support', 'embedding', 'other')",
        ),
        (
            "ck_agent_model_usage_event_status",
            "status IN ('succeeded', 'failed', 'cancelled')",
        ),
        (
            "ck_agent_model_usage_event_availability",
            "usage_availability IN ('complete', 'partial', 'unavailable')",
        ),
        (
            "ck_agent_model_usage_event_source",
            "usage_source IN ('provider_response', 'provider_headers', "
            "'platform_meter', 'not_available')",
        ),
        ("ck_agent_model_usage_event_provider_surface", "surface = 'provider_assistant'"),
        ("ck_agent_model_usage_event_provider_auth_tier", "auth_tier = 'provider'"),
        ("ck_agent_model_usage_event_call_sequence_positive", "call_sequence >= 1"),
        (
            "ck_agent_model_usage_event_latency_nonnegative",
            "latency_ms IS NULL OR latency_ms >= 0",
        ),
        (
            "ck_agent_model_usage_event_input_tokens_nonnegative",
            "input_tokens IS NULL OR input_tokens >= 0",
        ),
        (
            "ck_agent_model_usage_event_output_tokens_nonnegative",
            "output_tokens IS NULL OR output_tokens >= 0",
        ),
        (
            "ck_agent_model_usage_event_cached_tokens_nonnegative",
            "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
        ),
        (
            "ck_agent_model_usage_event_reasoning_tokens_nonnegative",
            "reasoning_tokens IS NULL OR reasoning_tokens >= 0",
        ),
    ):
        op.create_check_constraint(
            constraint_name,
            "agent_model_usage_event",
            condition,
        )
    for column_name in (
        "client_event_id",
        "interaction_id",
        "provider",
        "recorded_at",
        "workstream",
    ):
        op.create_index(
            op.f(f"agent_model_usage_event_{column_name}_idx"),
            "agent_model_usage_event",
            [column_name],
            unique=False,
        )

    op.create_table(
        "canonical_conversation_turn",
        _id("conversation_turn"),
        sa.Column(
            "interaction_id",
            activemodel.types.typeid.TypeIDType(prefix="interaction"),
            nullable=False,
        ),
        sa.Column("workload_client_id", sa.String(length=64), nullable=False),
        sa.Column("client_turn_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tenant_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_tenant"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=True,
        ),
        sa.Column(
            "person_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_person"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("canonical_conversation_turn_actor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interaction_lineage.id"],
            name=op.f("canonical_conversation_turn_interaction_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["authority_person.id"],
            name=op.f("canonical_conversation_turn_person_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["authority_tenant.id"],
            name=op.f("canonical_conversation_turn_tenant_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("canonical_conversation_turn_pkey")),
        comment="Immutable scope anchor shared by both canonical turn phases.",
    )
    op.create_index(
        op.f("canonical_conversation_turn_client_turn_id_idx"),
        "canonical_conversation_turn",
        ["client_turn_id"],
        unique=True,
    )
    op.create_index(
        op.f("canonical_conversation_turn_created_at_idx"),
        "canonical_conversation_turn",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("canonical_conversation_turn_interaction_id_idx"),
        "canonical_conversation_turn",
        ["interaction_id"],
        unique=True,
    )

    op.create_table(
        "canonical_conversation_user_phase",
        _id("conversation_user_phase"),
        sa.Column(
            "turn_id",
            activemodel.types.typeid.TypeIDType(prefix="conversation_turn"),
            nullable=False,
        ),
        sa.Column("ingress_kind", sa.String(length=32), nullable=False),
        sa.Column("action_code", sa.String(length=128), nullable=True),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(length=20000), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ingress_kind IN ('chat_text', 'provider_ui_action')",
            name="ck_canonical_conversation_user_phase_ingress_kind",
        ),
        sa.CheckConstraint(
            "(ingress_kind = 'chat_text' AND action_code IS NULL) OR "
            "(ingress_kind = 'provider_ui_action' AND action_code IS NOT NULL)",
            name="ck_canonical_conversation_user_phase_action_shape",
        ),
        sa.CheckConstraint(
            "content_type = 'text/plain'",
            name="ck_canonical_conversation_user_phase_content_type",
        ),
        sa.CheckConstraint(
            "length(content) BETWEEN 1 AND 20000",
            name="ck_canonical_conversation_user_phase_content_length",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["canonical_conversation_turn.id"],
            name=op.f("canonical_conversation_user_phase_turn_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("canonical_conversation_user_phase_pkey"),
        ),
        sa.UniqueConstraint(
            "turn_id",
            name="uq_canonical_conversation_user_phase_turn",
        ),
        comment="Append-only verbatim ingress accepted for one canonical turn.",
    )
    op.create_index(
        op.f("canonical_conversation_user_phase_recorded_at_idx"),
        "canonical_conversation_user_phase",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        op.f("canonical_conversation_user_phase_turn_id_idx"),
        "canonical_conversation_user_phase",
        ["turn_id"],
        unique=False,
    )

    op.create_table(
        "canonical_conversation_final_phase",
        _id("conversation_final_phase"),
        sa.Column(
            "turn_id",
            activemodel.types.typeid.TypeIDType(prefix="conversation_turn"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(length=20000), nullable=False),
        sa.Column("presentation_mode", sa.String(length=32), nullable=False),
        sa.Column("suppress_disclaimer", sa.Boolean(), nullable=False),
        sa.Column("suppress_intent_badge", sa.Boolean(), nullable=False),
        sa.Column("suppress_doctrinal_badge", sa.Boolean(), nullable=False),
        sa.Column("suppress_citations", sa.Boolean(), nullable=False),
        sa.Column("suppress_structured_cards", sa.Boolean(), nullable=False),
        sa.Column("suppress_feature_actions", sa.Boolean(), nullable=False),
        sa.Column("suppress_guided_paths", sa.Boolean(), nullable=False),
        sa.Column("human_contact_label", sa.String(length=120), nullable=True),
        sa.Column("human_contact_action_id", sa.String(length=128), nullable=True),
        sa.Column(
            "human_contact_fallback_text",
            sa.String(length=240),
            nullable=True,
        ),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('completed', 'refused', 'unavailable')",
            name="ck_canonical_conversation_final_phase_outcome",
        ),
        sa.CheckConstraint(
            "content_type = 'text/plain'",
            name="ck_canonical_conversation_final_phase_content_type",
        ),
        sa.CheckConstraint(
            "length(content) BETWEEN 1 AND 20000",
            name="ck_canonical_conversation_final_phase_content_length",
        ),
        sa.CheckConstraint(
            "presentation_mode IN "
            "('standard', 'emergency', 'sensitive_disclosure')",
            name="ck_canonical_conversation_final_phase_presentation_mode",
        ),
        sa.CheckConstraint(
            "(human_contact_label IS NULL AND human_contact_action_id IS NULL "
            "AND human_contact_fallback_text IS NULL) OR "
            "(human_contact_label IS NOT NULL "
            "AND human_contact_action_id IS NOT NULL "
            "AND human_contact_fallback_text IS NOT NULL)",
            name="ck_canonical_conversation_final_phase_contact_shape",
        ),
        sa.CheckConstraint(
            "human_contact_label IS NULL OR "
            "(length(human_contact_label) BETWEEN 1 AND 120 "
            "AND length(human_contact_action_id) BETWEEN 8 AND 128 "
            "AND length(human_contact_fallback_text) BETWEEN 1 AND 240)",
            name="ck_canonical_conversation_final_phase_contact_length",
        ),
        sa.CheckConstraint(
            "presentation_mode <> 'sensitive_disclosure' OR "
            "(suppress_disclaimer AND suppress_intent_badge "
            "AND suppress_doctrinal_badge AND suppress_citations "
            "AND suppress_structured_cards AND suppress_feature_actions "
            "AND suppress_guided_paths "
            "AND human_contact_label IS NOT NULL "
            "AND human_contact_action_id IS NOT NULL "
            "AND human_contact_fallback_text IS NOT NULL)",
            name="ck_canonical_conversation_final_phase_sensitive_shape",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["canonical_conversation_user_phase.turn_id"],
            name=op.f("canonical_conversation_final_phase_turn_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("canonical_conversation_final_phase_pkey"),
        ),
        sa.UniqueConstraint(
            "turn_id",
            name="uq_canonical_conversation_final_phase_turn",
        ),
        comment="Append-only exact response and presentation for one accepted turn.",
    )
    op.create_index(
        op.f("canonical_conversation_final_phase_recorded_at_idx"),
        "canonical_conversation_final_phase",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        op.f("canonical_conversation_final_phase_turn_id_idx"),
        "canonical_conversation_final_phase",
        ["turn_id"],
        unique=False,
    )


    op.create_table(
        "authority_audit_event",
        _id("authority_audit"),
        sa.Column("event_source", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workload_client_id", sa.String(length=64), nullable=True),
        sa.Column("client_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "tenant_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_tenant"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=True,
        ),
        sa.Column(
            "person_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_person"),
            nullable=True,
        ),
        sa.Column(
            "interaction_id",
            activemodel.types.typeid.TypeIDType(prefix="interaction"),
            nullable=True,
        ),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_ref", sa.String(length=160), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=True),
        sa.Column("route", sa.String(length=128), nullable=True),
        sa.Column("intent", sa.String(length=128), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "platform_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint(
            "event_source IN ('platform_authoritative', 'sovereign_observational')",
            name="ck_authority_audit_event_source",
        ),
        sa.CheckConstraint(
            "event_source <> 'sovereign_observational' OR "
            "(workload_client_id IS NOT NULL AND client_event_id IS NOT NULL "
            "AND interaction_id IS NOT NULL)",
            name="ck_authority_audit_observation_shape",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("authority_audit_event_actor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interaction_lineage.id"],
            name=op.f("authority_audit_event_interaction_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["authority_person.id"],
            name=op.f("authority_audit_event_person_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["authority_tenant.id"],
            name=op.f("authority_audit_event_tenant_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("authority_audit_event_pkey"),
        ),
        sa.UniqueConstraint(
            "workload_client_id",
            "interaction_id",
            "client_event_id",
            name="uq_authority_audit_observation_client_event",
        ),
        comment="Append-only, privacy-minimized authoritative platform audit fact.",
    )
    op.create_index(
        op.f("authority_audit_event_event_type_idx"),
        "authority_audit_event",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("authority_audit_event_occurred_at_idx"),
        "authority_audit_event",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("authority_audit_event_recorded_at_idx"),
        "authority_audit_event",
        ["recorded_at"],
        unique=False,
    )

    # These records are authority facts, not mutable application state.  ORM
    # listeners protect normal code paths; database triggers also reject direct
    # SQL issued by another process using the application database role.
    op.execute(
        """
        CREATE FUNCTION authority_reject_append_only_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'authority records are append-only';
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER authority_person_origin_append_only
        BEFORE UPDATE OR DELETE ON authority_person_origin
        FOR EACH ROW EXECUTE FUNCTION authority_reject_append_only_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER authority_audit_event_append_only
        BEFORE UPDATE OR DELETE ON authority_audit_event
        FOR EACH ROW EXECUTE FUNCTION authority_reject_append_only_mutation()
        """
    )
    for table_name in (
        "agent_model_usage_event",
        "canonical_conversation_turn",
        "canonical_conversation_user_phase",
        "canonical_conversation_final_phase",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION authority_reject_append_only_mutation()
            """
        )


def downgrade() -> None:
    _refuse_populated_or_offline_downgrade()
    for table_name in (
        "canonical_conversation_final_phase",
        "canonical_conversation_user_phase",
        "canonical_conversation_turn",
        "agent_model_usage_event",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_append_only ON {table_name}"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS authority_audit_event_append_only "
        "ON authority_audit_event"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS authority_person_origin_append_only "
        "ON authority_person_origin"
    )
    op.execute("DROP FUNCTION IF EXISTS authority_reject_append_only_mutation()")
    op.drop_table("authority_audit_event")
    op.drop_table("agent_model_usage_event")
    op.drop_table("canonical_conversation_final_phase")
    op.drop_table("canonical_conversation_user_phase")
    op.drop_table("canonical_conversation_turn")
    op.drop_table("authority_idempotency_record")
    op.drop_table("interaction_lineage")
    op.drop_table("mount_context_token")
    op.drop_table("mount_context_session")
    op.drop_table("authority_person_binding")
    op.drop_table("authority_person_origin")
    op.drop_table("authority_tenant_membership")
    op.drop_table("authority_actor_external_identity")
    op.drop_table("authority_person")
    op.drop_table("authority_actor")
    op.drop_table("authority_tenant")
