"""add_account_owned_provider_authority

Revision ID: ddd6261fa36f
Revises: 49126bf99c72
Create Date: 2026-11-03 10:13:30.329035

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import activemodel


# revision identifiers, used by Alembic.
revision: str = 'ddd6261fa36f'
down_revision: Union[str, None] = '49126bf99c72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add a private namespace only; no account/practice/legal/billing rows or
    # existing immutable provider bindings are rewritten by this expand rung.
    op.execute("SET LOCAL lock_timeout = '4s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.add_column(
        "provider_practice_authority",
        sa.Column(
            "doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=True,
        ),
    )
    op.alter_column(
        "provider_practice_authority", "practice_id",
        existing_type=sa.UUID(), nullable=True,
    )
    op.create_unique_constraint(
        op.f("provider_practice_authority_doctor_id_key"),
        "provider_practice_authority", ["doctor_id"],
    )
    op.execute(
        "ALTER TABLE provider_practice_authority "
        "ADD CONSTRAINT provider_practice_authority_doctor_id_fkey "
        "FOREIGN KEY (doctor_id) REFERENCES doctor(id) NOT VALID"
    )
    op.execute(
        "ALTER TABLE provider_practice_authority "
        "VALIDATE CONSTRAINT provider_practice_authority_doctor_id_fkey"
    )
    op.execute(
        "ALTER TABLE provider_practice_authority "
        "ADD CONSTRAINT ck_provider_practice_authority_exactly_one_owner "
        "CHECK ((practice_id IS NOT NULL) <> (doctor_id IS NOT NULL)) NOT VALID"
    )
    op.execute(
        "ALTER TABLE provider_practice_authority "
        "VALIDATE CONSTRAINT ck_provider_practice_authority_exactly_one_owner"
    )
    op.create_table_comment(
        "provider_practice_authority",
        "One immutable provider namespace, owned by a practice or one account.",
        existing_comment=None,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Leave account-owned provider authority structures dormant on rollback; "
        "removing immutable namespace evidence requires a separate operation."
    )
