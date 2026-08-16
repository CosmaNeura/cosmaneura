"""add_authority_kernel

Revision ID: a61c2f1e9b44
Revises: 4f7a9c2d1e80
Create Date: 2026-08-15 20:00:00.000000
"""

from collections.abc import Sequence

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "a61c2f1e9b44"
down_revision: str | None = "7c1d5e9a2b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
            "idempotency_key",
            name="uq_authority_idempotency_scope_key",
        ),
        sa.UniqueConstraint(
            "workload_client_id",
            "operation",
            "interaction_id",
            name="uq_authority_idempotency_logical_operation",
        ),
        comment="Request-digest binding for future consequence-bearing Agent API writes.",
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
        "authority_audit_event",
        _id("authority_audit"),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
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


def downgrade() -> None:
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
