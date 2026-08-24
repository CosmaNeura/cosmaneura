"""Add capability-bound support view-as sessions and audit events.

Revision ID: 89c31f0ae275
Revises: 6a91d2f4c8e0
Create Date: 2026-08-24 12:00:00.000000
"""

from typing import Sequence, Union

import activemodel.types.typeid
import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "89c31f0ae275"
down_revision: Union[str, None] = "6a91d2f4c8e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
        "view_as_session",
        _id("view_as_session"),
        *_timestamps(),
        sa.Column(
            "environment", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column(
            "capability_grant_id",
            activemodel.types.typeid.TypeIDType(prefix="superadmin_capability"),
            nullable=False,
        ),
        sa.Column(
            "actor_doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column(
            "target_doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column("actor_issuer_sha256", sa.String(length=64), nullable=False),
        sa.Column("actor_subject_sha256", sa.String(length=64), nullable=False),
        sa.Column("clerk_session_sha256", sa.String(length=64), nullable=False),
        sa.Column("browser_nonce_sha256", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name=op.f("view_as_session_ck_view_as_session_environment"),
        ),
        sa.CheckConstraint(
            "actor_doctor_id <> target_doctor_id",
            name=op.f("view_as_session_ck_view_as_session_distinct_doctors"),
        ),
        sa.CheckConstraint(
            "actor_issuer_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("view_as_session_ck_view_as_session_actor_issuer_sha256"),
        ),
        sa.CheckConstraint(
            "actor_subject_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("view_as_session_ck_view_as_session_actor_subject_sha256"),
        ),
        sa.CheckConstraint(
            "clerk_session_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("view_as_session_ck_view_as_session_clerk_session_sha256"),
        ),
        sa.CheckConstraint(
            "browser_nonce_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("view_as_session_ck_view_as_session_browser_nonce_sha256"),
        ),
        sa.CheckConstraint(
            "absolute_expires_at > started_at",
            name=op.f("view_as_session_ck_view_as_session_expiry_after_start"),
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_at >= started_at "
            "AND end_reason IS NOT NULL)",
            name=op.f("view_as_session_ck_view_as_session_end_complete"),
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN "
            "('operator_exit', 'rotated', 'expired', "
            "'capability_revoked', 'session_binding_changed')",
            name=op.f("view_as_session_ck_view_as_session_end_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["capability_grant_id"],
            ["superadmin_capability_grant.id"],
            name=op.f("view_as_session_capability_grant_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_doctor_id"],
            ["doctor.id"],
            name=op.f("view_as_session_actor_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["target_doctor_id"],
            ["doctor.id"],
            name=op.f("view_as_session_target_doctor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("view_as_session_pkey")),
        comment="One short-lived, server-side actor-to-target support session.",
    )
    op.create_index(
        "uq_view_as_session_active_actor",
        "view_as_session",
        ["environment", "actor_doctor_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "view_as_session_expiry_idx",
        "view_as_session",
        ["absolute_expires_at"],
        unique=False,
    )

    op.create_table(
        "view_as_audit_event",
        _id("view_as_audit"),
        *_timestamps(),
        sa.Column(
            "environment", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column(
            "event_type", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False
        ),
        sa.Column(
            "outcome", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column(
            "reason_code", sqlmodel.sql.sqltypes.AutoString(length=48), nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "event_digest", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column(
            "session_id",
            activemodel.types.typeid.TypeIDType(prefix="view_as_session"),
            nullable=True,
        ),
        sa.Column(
            "capability_grant_id",
            activemodel.types.typeid.TypeIDType(prefix="superadmin_capability"),
            nullable=True,
        ),
        sa.Column(
            "actor_doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column(
            "target_doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name=op.f("view_as_audit_event_ck_view_as_audit_environment"),
        ),
        sa.CheckConstraint(
            "event_type IN "
            "('began', 'ended', 'expired', 'stale_cleared', 'rejected')",
            name=op.f("view_as_audit_event_ck_view_as_audit_event_type"),
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'denied')",
            name=op.f("view_as_audit_event_ck_view_as_audit_outcome"),
        ),
        sa.CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("view_as_audit_event_ck_view_as_audit_event_digest"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["view_as_session.id"],
            name=op.f("view_as_audit_event_session_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["capability_grant_id"],
            ["superadmin_capability_grant.id"],
            name=op.f("view_as_audit_event_capability_grant_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_doctor_id"],
            ["doctor.id"],
            name=op.f("view_as_audit_event_actor_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["target_doctor_id"],
            ["doctor.id"],
            name=op.f("view_as_audit_event_target_doctor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("view_as_audit_event_pkey")),
        comment="Append-only, value-minimized view-as lifecycle evidence.",
    )
    op.create_index(
        op.f("view_as_audit_event_event_type_idx"),
        "view_as_audit_event",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "view_as_audit_occurred_at_idx",
        "view_as_audit_event",
        ["occurred_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION cosmas_guard_view_as_session_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'view-as session evidence is retained'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.ended_at IS NOT NULL THEN
            RAISE EXCEPTION 'ended view-as sessions are immutable'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.environment IS DISTINCT FROM OLD.environment
             OR NEW.capability_grant_id IS DISTINCT FROM OLD.capability_grant_id
             OR NEW.actor_doctor_id IS DISTINCT FROM OLD.actor_doctor_id
             OR NEW.target_doctor_id IS DISTINCT FROM OLD.target_doctor_id
             OR NEW.actor_issuer_sha256 IS DISTINCT FROM OLD.actor_issuer_sha256
             OR NEW.actor_subject_sha256 IS DISTINCT FROM OLD.actor_subject_sha256
             OR NEW.clerk_session_sha256 IS DISTINCT FROM OLD.clerk_session_sha256
             OR NEW.browser_nonce_sha256 IS DISTINCT FROM OLD.browser_nonce_sha256
             OR NEW.started_at IS DISTINCT FROM OLD.started_at
             OR NEW.absolute_expires_at IS DISTINCT FROM OLD.absolute_expires_at
             OR NEW.ended_at IS NULL
             OR NEW.end_reason IS NULL THEN
            RAISE EXCEPTION 'view-as sessions may only transition to ended'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER view_as_session_guard_mutation
        BEFORE UPDATE OR DELETE ON view_as_session
        FOR EACH ROW EXECUTE FUNCTION cosmas_guard_view_as_session_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION cosmas_reject_view_as_audit_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'view-as audit events are append-only'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER view_as_audit_event_append_only
        BEFORE UPDATE OR DELETE ON view_as_audit_event
        FOR EACH ROW EXECUTE FUNCTION cosmas_reject_view_as_audit_mutation()
        """
    )


def downgrade() -> None:
    op.execute("LOCK TABLE view_as_audit_event IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE view_as_session IN ACCESS EXCLUSIVE MODE")
    audit_count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM view_as_audit_event")
    ).scalar_one()
    session_count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM view_as_session")
    ).scalar_one()
    if audit_count or session_count:
        raise RuntimeError(
            "refusing to drop retained view-as evidence: "
            f"session_count={session_count} audit_count={audit_count}"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS view_as_audit_event_append_only "
        "ON view_as_audit_event"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_reject_view_as_audit_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS view_as_session_guard_mutation ON view_as_session"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_guard_view_as_session_mutation()")
    op.drop_index("view_as_audit_occurred_at_idx", table_name="view_as_audit_event")
    op.drop_index(
        op.f("view_as_audit_event_event_type_idx"),
        table_name="view_as_audit_event",
    )
    op.drop_table("view_as_audit_event")
    op.drop_index("view_as_session_expiry_idx", table_name="view_as_session")
    op.drop_index(
        "uq_view_as_session_active_actor",
        table_name="view_as_session",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_table("view_as_session")
