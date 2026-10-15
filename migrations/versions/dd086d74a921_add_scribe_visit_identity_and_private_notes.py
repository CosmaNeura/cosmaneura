"""Expand author-only reminders and provider-confirmed patient associations.

No backfill, source relinking, clinical content rewrite, or data deletion.
"""

from alembic import op
import sqlalchemy as sa
from activemodel.types import TypeIDType

revision = 'dd086d74a921'
down_revision = '35260adbf2ef'
branch_labels = None
depends_on = None


def common(prefix):
    return [
        sa.Column('id', TypeIDType(prefix), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('appointment_id', TypeIDType('appointment'), nullable=False),
        sa.Column('actor_id', TypeIDType('authority_actor'), nullable=False),
        sa.Column('tenant_id', TypeIDType('authority_tenant'), nullable=False),
        sa.Column('provider_link_id', TypeIDType('scribe_provider_link'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['appointment_id'], ['appointment.id']),
        sa.ForeignKeyConstraint(['actor_id'], ['authority_actor.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['authority_tenant.id']),
        sa.ForeignKeyConstraint(['provider_link_id'], ['scribe_capture_provider_link.id']),
    ]


def upgrade():
    op.create_table('scribe_private_note', *common('scribe_private_note'),
        sa.Column('doctor_id', TypeIDType('doctor'), nullable=False),
        # Only authenticated ciphertext is stored; encryption occurs in the app.
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('saved_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['doctor_id'], ['doctor.id']),
        sa.UniqueConstraint('appointment_id', 'actor_id', 'tenant_id', 'provider_link_id', name='uq_scribe_private_note_author_visit'),
        sa.CheckConstraint('version > 0', name='ck_scribe_private_note_version'),
    )
    op.create_table('scribe_private_note_receipt', *common('scribe_private_receipt'),
        sa.Column('change_key', sa.Uuid(), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('saved_version', sa.Integer(), nullable=False),
        sa.UniqueConstraint('actor_id', 'tenant_id', 'change_key', name='uq_scribe_private_note_change'),
    )
    op.create_table('scribe_patient_identity', *common('scribe_patient_identity'),
        sa.Column('doctor_id', TypeIDType('doctor'), nullable=False),
        sa.Column('source_patient_id', TypeIDType('patient'), nullable=False),
        sa.Column('canonical_patient_id', TypeIDType('patient'), nullable=False),
        sa.Column('provisional_entry', sa.Boolean(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['doctor_id'], ['doctor.id']),
        sa.ForeignKeyConstraint(['source_patient_id'], ['patient.id']),
        sa.ForeignKeyConstraint(['canonical_patient_id'], ['patient.id']),
        sa.UniqueConstraint('appointment_id', name='uq_scribe_patient_identity_visit'),
        sa.CheckConstraint("state IN ('provisional', 'matched', 'confirmed')", name='ck_scribe_patient_identity_state'),
        sa.CheckConstraint('version >= 0', name='ck_scribe_patient_identity_version'),
    )
    op.create_index('scribe_patient_identity_canonical_patient_id_idx', 'scribe_patient_identity', ['canonical_patient_id'])


def downgrade():
    raise RuntimeError('Disable visit extras; retain private notes, identity decisions and retry evidence')
