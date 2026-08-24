"""Add append-only pilot access continuity amendment evidence.

Revision ID: 02b1cd080521
Revises: 6a91d2f4c8e0
Create Date: 2026-08-24 15:22:52.300712
"""

from typing import Sequence, Union

import activemodel.types.typeid
import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "02b1cd080521"
down_revision: Union[str, None] = "6a91d2f4c8e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pilot_access_continuity_amendment",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="pilot_access_amendment"),
            nullable=False,
            comment="TypeID with prefix: pilot_access_amendment",
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
            "environment", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False
        ),
        sa.Column(
            "clerk_issuer",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "source_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "manifest_sha256",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "plan_sha256",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "applied_by_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("account_count", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name=op.f(
                "pilot_access_continuity_amendment_ck_pilot_access_amendment_environment_check"
            ),
        ),
        sa.CheckConstraint(
            "clerk_issuer = btrim(clerk_issuer) "
            "AND clerk_issuer LIKE 'https://%' "
            "AND right(clerk_issuer, 1) <> '/'",
            name=op.f(
                "pilot_access_continuity_amendment_ck_pilot_access_amendment_clerk_issuer_check"
            ),
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "pilot_access_continuity_amendment_ck_pilot_access_amendment_manifest_sha256_check"
            ),
        ),
        sa.CheckConstraint(
            "plan_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "pilot_access_continuity_amendment_ck_pilot_access_amendment_plan_sha256_check"
            ),
        ),
        sa.CheckConstraint(
            "account_count > 0",
            name=op.f(
                "pilot_access_continuity_amendment_ck_pilot_access_amendment_account_count_check"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pilot_access_continuity_amendment_pkey")
        ),
        sa.UniqueConstraint(
            "plan_sha256",
            name=op.f("pilot_access_continuity_amendment_plan_sha256_key"),
        ),
        comment="One hash-confirmed, environment-bound omission amendment batch.",
    )
    op.create_table(
        "pilot_access_continuity_amendment_identity",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(
                prefix="pilot_access_amendment_identity"
            ),
            nullable=False,
            comment="TypeID with prefix: pilot_access_amendment_identity",
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
            "amendment_id",
            activemodel.types.typeid.TypeIDType(prefix="pilot_access_amendment"),
            nullable=False,
        ),
        sa.Column(
            "grant_id",
            activemodel.types.typeid.TypeIDType(prefix="pilot_access_grant"),
            nullable=False,
        ),
        sa.Column(
            "clerk_issuer",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "clerk_subject",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "email_sha256",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "doctor_id_snapshot",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=True,
        ),
        sa.Column(
            "practice_id_snapshot",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=True,
        ),
        sa.Column(
            "safe_prelogin",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "continuity_basis",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.CheckConstraint(
            "clerk_issuer = btrim(clerk_issuer) "
            "AND clerk_issuer LIKE 'https://%' "
            "AND right(clerk_issuer, 1) <> '/'",
            name=op.f(
                "pilot_access_continuity_amendment_identity_ck_pilot_access_amendment_identity_clerk_issuer_check"
            ),
        ),
        sa.CheckConstraint(
            "left(clerk_subject, 5) = 'user_'",
            name=op.f(
                "pilot_access_continuity_amendment_identity_ck_pilot_access_amendment_identity_clerk_subject_check"
            ),
        ),
        sa.CheckConstraint(
            "email_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "pilot_access_continuity_amendment_identity_ck_pilot_access_amendment_identity_email_sha256_check"
            ),
        ),
        sa.CheckConstraint(
            "continuity_basis = 'reviewed_operational_user_omission'",
            name=op.f(
                "pilot_access_continuity_amendment_identity_ck_pilot_access_amendment_identity_basis_check"
            ),
        ),
        sa.CheckConstraint(
            "(safe_prelogin IS TRUE AND doctor_id_snapshot IS NULL) OR "
            "(safe_prelogin IS FALSE AND doctor_id_snapshot IS NOT NULL)",
            name=op.f(
                "pilot_access_continuity_amendment_identity_ck_pilot_access_amendment_identity_account_state_check"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["amendment_id"],
            ["pilot_access_continuity_amendment.id"],
            name=op.f(
                "pilot_access_continuity_amendment_identity_amendment_id_fkey"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id_snapshot"],
            ["doctor.id"],
            name=op.f(
                "pilot_access_continuity_amendment_identity_doctor_id_snapshot_fkey"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["pilot_access_grandfather_grant.id"],
            name=op.f("pilot_access_continuity_amendment_identity_grant_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["practice_id_snapshot"],
            ["practice.id"],
            name=op.f(
                "pilot_access_continuity_amendment_identity_practice_id_snapshot_fkey"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pilot_access_continuity_amendment_identity_pkey")
        ),
        sa.UniqueConstraint(
            "amendment_id",
            "clerk_subject",
            name="uq_pilot_access_amendment_identity_subject",
        ),
        sa.UniqueConstraint(
            "grant_id",
            name=op.f("pilot_access_continuity_amendment_identity_grant_id_key"),
        ),
        comment="Exact identity evidence for one omission grant.",
    )
    op.create_index(
        "uq_pilot_access_amendment_identity_issuer_subject",
        "pilot_access_continuity_amendment_identity",
        ["clerk_issuer", "clerk_subject"],
        unique=True,
    )
    op.create_index(
        "uq_pilot_access_amendment_identity_email_sha256",
        "pilot_access_continuity_amendment_identity",
        ["email_sha256"],
        unique=True,
    )
    op.create_index(
        "uq_pilot_access_amendment_identity_doctor",
        "pilot_access_continuity_amendment_identity",
        ["doctor_id_snapshot"],
        unique=True,
        postgresql_where=sa.text("doctor_id_snapshot IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION cosmas_reject_pilot_access_amendment_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION
            'pilot access continuity amendment evidence is append-only'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table in (
        "pilot_access_continuity_amendment",
        "pilot_access_continuity_amendment_identity",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_reject_mutation "
            f"BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION cosmas_reject_pilot_access_amendment_mutation()"
        )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE pilot_access_continuity_amendment, "
        "pilot_access_continuity_amendment_identity IN ACCESS EXCLUSIVE MODE"
    )
    connection = op.get_bind()
    batch_count = connection.execute(
        sa.text("SELECT count(*) FROM pilot_access_continuity_amendment")
    ).scalar_one()
    identity_count = connection.execute(
        sa.text("SELECT count(*) FROM pilot_access_continuity_amendment_identity")
    ).scalar_one()
    if batch_count or identity_count:
        raise RuntimeError(
            "refusing to drop retained pilot continuity amendment evidence: "
            f"batches={batch_count}, identities={identity_count}"
        )
    for table in (
        "pilot_access_continuity_amendment_identity",
        "pilot_access_continuity_amendment",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_mutation ON {table}")
    op.execute(
        "DROP FUNCTION IF EXISTS cosmas_reject_pilot_access_amendment_mutation()"
    )
    op.drop_index(
        "uq_pilot_access_amendment_identity_doctor",
        table_name="pilot_access_continuity_amendment_identity",
        postgresql_where=sa.text("doctor_id_snapshot IS NOT NULL"),
    )
    op.drop_index(
        "uq_pilot_access_amendment_identity_email_sha256",
        table_name="pilot_access_continuity_amendment_identity",
    )
    op.drop_index(
        "uq_pilot_access_amendment_identity_issuer_subject",
        table_name="pilot_access_continuity_amendment_identity",
    )
    op.drop_table("pilot_access_continuity_amendment_identity")
    op.drop_table("pilot_access_continuity_amendment")
