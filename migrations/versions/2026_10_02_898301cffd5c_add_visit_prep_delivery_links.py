"""add_visit_prep_delivery_links

Revision ID: 898301cffd5c
Revises: 85d330017454
Create Date: 2026-10-02 23:21:09.251543

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

# revision identifiers, used by Alembic.
revision: str = '898301cffd5c'
down_revision: Union[str, None] = '85d330017454'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated on owned PostgreSQL; unrelated pre-existing comment drift removed.
    op.create_table('visit_prep_delivery_link',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_link'), nullable=False, comment='TypeID with prefix: visit_prep_link'),
    sa.Column('visit_prep_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('question_set_version_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_question_set_version'), nullable=False),
    sa.Column('credential_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_credential'), nullable=False),
    sa.Column('operation_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('payload_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('evidence_ciphertext', sa.Text(), nullable=False),
    sa.Column('envelope_format_version', sa.Integer(), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("expires_at > issued_at AND expires_at <= issued_at + interval '24 hours'", name=op.f('visit_prep_delivery_link_time_shape_check')),
    sa.CheckConstraint("substring(operation_id::text FROM 15 FOR 1)='4' AND substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')", name=op.f('visit_prep_delivery_link_operation_shape_check')),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$' AND payload_hmac ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_delivery_link_digest_shape_check')),
    sa.CheckConstraint('envelope_format_version=1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 4096', name=op.f('visit_prep_delivery_link_envelope_shape_check')),
    sa.ForeignKeyConstraint(['credential_id'], ['visit_prep_credential_generation.id'], name='fk_visit_prep_link_credential', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['visit_prep_id', 'owner_practice_member_id', 'question_set_version_id'], ['visit_prep.id', 'visit_prep.owner_practice_member_id', 'visit_prep.question_set_version_id'], name='fk_visit_prep_link_root', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_delivery_link_pkey')),
    sa.UniqueConstraint('token_hash', name='uq_visit_prep_link_token'),
    sa.UniqueConstraint('visit_prep_id', 'operation_id', name='uq_visit_prep_link_operation')
    )
    op.execute("""
      CREATE FUNCTION public.cosmas_guard_visit_prep_delivery_link()
      RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
      DECLARE root public.visit_prep%ROWTYPE; credential public.visit_prep_credential_generation%ROWTYPE;
        checked_at timestamptz; current_id uuid;
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'Delivery link evidence is immutable' USING ERRCODE='55000';
        END IF;
        PERFORM 1 FROM public.practice p JOIN public.visit_prep v ON v.practice_id=p.id
          WHERE v.id=NEW.visit_prep_id FOR UPDATE OF p;
        SELECT * INTO root FROM public.visit_prep WHERE id=NEW.visit_prep_id FOR UPDATE;
        SELECT * INTO credential FROM public.visit_prep_credential_generation WHERE id=NEW.credential_id FOR UPDATE;
        SELECT id INTO current_id FROM public.visit_prep_credential_generation
          WHERE visit_prep_id=root.id ORDER BY generation DESC LIMIT 1;
        checked_at := clock_timestamp();
        IF root.id IS NULL OR credential.id IS NULL OR credential.id <> current_id
          OR credential.visit_prep_id <> root.id OR credential.owner_practice_member_id <> root.owner_practice_member_id
          OR NEW.owner_practice_member_id <> root.owner_practice_member_id
          OR NEW.question_set_version_id <> root.question_set_version_id
          OR root.revoked_at IS NOT NULL OR root.misdirected_at IS NOT NULL OR root.completed_at IS NOT NULL
          OR credential.revoked_at IS NOT NULL OR NEW.issued_at < credential.issued_at
          OR NEW.issued_at > checked_at OR NEW.expires_at <= checked_at
          OR NEW.expires_at > credential.expires_at
          OR NEW.expires_at > (SELECT scheduled_at FROM public.visit_prep_schedule_generation WHERE id=credential.schedule_generation_id)
          OR EXISTS (SELECT 1 FROM public.visit_prep_credential_generation WHERE token_hash=NEW.token_hash) THEN
          RAISE EXCEPTION 'Delivery link context is unavailable' USING ERRCODE='55000';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER visit_prep_delivery_link_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.visit_prep_delivery_link FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_delivery_link();
      CREATE TRIGGER visit_prep_delivery_link_no_truncate BEFORE TRUNCATE
        ON public.visit_prep_delivery_link FOR EACH STATEMENT EXECUTE FUNCTION public.cosmas_guard_visit_prep_delivery_link();
    """)


def downgrade() -> None:
    raise RuntimeError('Retain delivery link evidence; use a forward fix')
