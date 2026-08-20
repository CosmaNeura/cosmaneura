"""add baa versions and acceptances

Clickwrap execution records for the counsel-approved BAA (CN-BAA-CUST-CW).
baa_acceptance is append-only by design: no application code updates or
deletes rows, and retention is indefinite (45 C.F.R. § 164.316(b)(2) floor).
Also adds the enterprise negotiated-BAA fallback fields to practice and
seeds the v2.2 version row.

Revision ID: bfd0ced59a81
Revises: ffd2f37f0d11
Create Date: 2026-08-20 12:10:00.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "bfd0ced59a81"
down_revision: Union[str, None] = "ffd2f37f0d11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BAA_V22_SHA256 = "13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e"
BAA_V22_PATH = "app/assets/baa/baa_cn-baa-cust-cw_v2.2.html"


def upgrade() -> None:
    op.create_table(
        "baa_version",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="baa_version"),
            nullable=False,
            comment="TypeID with prefix: baa_version",
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
        sa.Column("doc_id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column(
            "content_path",
            sa.String(),
            nullable=False,
            comment="Repo-relative path of the canonical HTML asset.",
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_current", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("baa_version_pkey")),
        sa.UniqueConstraint("doc_id", "version", name="baa_version_doc_version_key"),
        comment="One row per published BAA version. Text changes are new rows, never edits.",
    )
    op.create_table(
        "baa_acceptance",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="baa_acceptance"),
            nullable=False,
            comment="TypeID with prefix: baa_acceptance",
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
            "practice_id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
        ),
        sa.Column(
            "baa_version_id",
            activemodel.types.typeid.TypeIDType(prefix="baa_version"),
            nullable=False,
        ),
        sa.Column("covered_entity_legal_name", sa.String(), nullable=False),
        sa.Column(
            "accepted_by_doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column("accepted_by_name", sa.String(), nullable=False),
        sa.Column("accepted_by_title", sa.String(), nullable=False),
        sa.Column("accepted_by_email", sa.String(), nullable=False),
        sa.Column(
            "accepted_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Server-generated; the client clock is never trusted.",
        ),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "content_sha256",
            sa.String(),
            nullable=False,
            comment="Hash of the exact text presented at acceptance.",
        ),
        sa.ForeignKeyConstraint(
            ["practice_id"],
            ["practice.id"],
            name=op.f("baa_acceptance_practice_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["baa_version_id"],
            ["baa_version.id"],
            name=op.f("baa_acceptance_baa_version_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_doctor_id"],
            ["doctor.id"],
            name=op.f("baa_acceptance_accepted_by_doctor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("baa_acceptance_pkey")),
        comment="Append-only clickwrap execution record; never updated or deleted.",
    )
    op.add_column(
        "practice",
        sa.Column(
            "negotiated_baa_on_file",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="Enterprise fallback: a countersigned CN-BAA-CUST is on file instead of clickwrap.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "negotiated_baa_effective_date", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "negotiated_baa_reference",
            sa.String(),
            nullable=True,
            comment="Document reference for the negotiated instrument (internal-admin set).",
        ),
    )

    # TypeID columns store as native UUIDs; the prefix is app-side only
    op.execute(
        sa.text(
            """
            INSERT INTO baa_version
                (id, doc_id, version, sha256, content_path, published_at, is_current)
            VALUES
                (gen_random_uuid(), 'CN-BAA-CUST-CW', '2.2', :sha, :path, now(), true)
            ON CONFLICT ON CONSTRAINT baa_version_doc_version_key DO NOTHING
            """
        ).bindparams(sha=BAA_V22_SHA256, path=BAA_V22_PATH)
    )


def downgrade() -> None:
    op.drop_column("practice", "negotiated_baa_reference")
    op.drop_column("practice", "negotiated_baa_effective_date")
    op.drop_column("practice", "negotiated_baa_on_file")
    op.drop_table("baa_acceptance")
    op.drop_table("baa_version")
