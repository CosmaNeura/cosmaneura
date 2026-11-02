"""add_pilot_practice_binding_evidence

Revision ID: 6bd4416fe39c
Revises: 312b217ebba4
Create Date: 2026-11-02 11:20:16.035840

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from activemodel.types import TypeIDType


# revision identifiers, used by Alembic.
revision: str = '6bd4416fe39c'
down_revision: Union[str, None] = '312b217ebba4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pilot_access_practice_binding",
        sa.Column("id", TypeIDType("pilot_practice_binding"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("predecessor_grant_id", TypeIDType("pilot_access_grant"), nullable=False),
        sa.Column("successor_grant_id", TypeIDType("pilot_access_grant"), nullable=False),
        sa.Column("receipt_id", TypeIDType("practice_access_audit"), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["predecessor_grant_id"], ["pilot_access_grandfather_grant.id"]),
        sa.ForeignKeyConstraint(["successor_grant_id"], ["pilot_access_grandfather_grant.id"]),
        sa.ForeignKeyConstraint(["receipt_id"], ["practice_access_audit.id"]),
        sa.UniqueConstraint("predecessor_grant_id"),
        sa.UniqueConstraint("successor_grant_id"),
        sa.UniqueConstraint("receipt_id"),
        sa.UniqueConstraint("plan_sha256"),
        sa.CheckConstraint("predecessor_grant_id <> successor_grant_id", name="ck_pilot_practice_binding_distinct_grants"),
        sa.CheckConstraint("environment IN ('staging', 'production')", name="ck_pilot_practice_binding_environment"),
        sa.CheckConstraint("plan_sha256 ~ '^[0-9a-f]{64}$'", name="ck_pilot_practice_binding_plan_digest"),
    )
    op.execute("""
        CREATE FUNCTION cosmas_reject_pilot_practice_binding_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'pilot practice binding evidence is append-only'
            USING ERRCODE = '55000';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER pilot_access_practice_binding_reject_mutation
        BEFORE UPDATE OR DELETE ON pilot_access_practice_binding FOR EACH ROW
        EXECUTE FUNCTION cosmas_reject_pilot_practice_binding_mutation()
    """)


def downgrade() -> None:
    # Retained lineage cannot be discarded, including on an empty environment.
    raise RuntimeError("keep the additive pilot practice binding evidence table; restore compatible readers")
