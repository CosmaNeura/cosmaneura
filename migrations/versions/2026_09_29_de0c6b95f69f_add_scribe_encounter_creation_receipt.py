"""Add atomic provider-entered encounter receipts. Retain on rollback."""
from alembic import op
import sqlalchemy as sa
from activemodel.types import TypeIDType

revision = 'de0c6b95f69f'
down_revision = '9a23eb377df7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('scribe_encounter_creation',
        sa.Column('id', TypeIDType('scribe_encounter_creation'), nullable=False,
            comment='TypeID with prefix: scribe_encounter_creation'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('doctor_id', TypeIDType('doctor'), nullable=False),
        sa.Column('provider_link_id', TypeIDType('scribe_provider_link'), nullable=False),
        sa.Column('patient_id', TypeIDType('patient'), nullable=False),
        sa.Column('appointment_id', TypeIDType('appointment'), nullable=False),
        sa.Column('creation_key', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('request_sha256', sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['doctor_id'], ['doctor.id']),
        sa.ForeignKeyConstraint(['provider_link_id'], ['scribe_capture_provider_link.id']),
        sa.ForeignKeyConstraint(['patient_id'], ['patient.id']),
        sa.ForeignKeyConstraint(['appointment_id'], ['appointment.id']),
        sa.UniqueConstraint('doctor_id', 'creation_key', name='uq_scribe_encounter_creation_key'),
        sa.UniqueConstraint('appointment_id', name='uq_scribe_encounter_creation_appointment'),
        sa.UniqueConstraint('workspace_id', name='uq_scribe_encounter_creation_workspace'),
        sa.CheckConstraint('length(request_sha256) = 64', name='ck_scribe_encounter_creation_digest'),
    )
    op.create_index('scribe_encounter_creation_doctor_id_idx', 'scribe_encounter_creation', ['doctor_id'])


def downgrade():
    raise RuntimeError('Disable encounter creation; retain visits and retry receipts')
