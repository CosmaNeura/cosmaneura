"""Add the dormant Visit Prep aggregate and immutable generation history.

Revision ID: 593ea9c376f4
Revises: 271e4439bff6
Create Date: 2026-08-25 21:05:36.166568

"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "593ea9c376f4"
down_revision: Union[str, None] = "271e4439bff6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AGGREGATE_TABLES_ROOT_FIRST = (
    "visit_prep",
    "visit_prep_legal_hold",
    "visit_prep_schedule_generation",
    "visit_prep_credential_generation",
)

_AGGREGATE_TABLES_CHILD_FIRST = (
    "visit_prep_credential_generation",
    "visit_prep_schedule_generation",
    "visit_prep_legal_hold",
    "visit_prep",
)

_CANDIDATE_KEY_INDEXES = (
    "uq_appointment_id_doctor_patient",
    "uq_practice_member_id_practice_doctor",
)


def _refuse_populated_or_offline_downgrade() -> None:
    """Only permit expand rollback while every new table is still unused."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "Visit Prep aggregate downgrade is unsupported in offline mode"
        )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE "
            + ", ".join(
                f'public."{table_name}"'
                for table_name in _AGGREGATE_TABLES_ROOT_FIRST
            )
            + " IN ACCESS EXCLUSIVE MODE"
        )
    )
    populated = [
        table_name
        for table_name in _AGGREGATE_TABLES_CHILD_FIRST
        if connection.execute(
            sa.text(
                f'SELECT EXISTS (SELECT 1 FROM public."{table_name}" LIMIT 1)'
            )
        ).scalar_one()
    ]
    if populated:
        raise RuntimeError(
            "Visit Prep aggregate downgrade refused because durable records "
            "exist in: " + ", ".join(populated)
        )


def _add_existing_table_candidate_keys() -> None:
    """Build redundant FK target indexes without blocking table writes."""

    context = op.get_context()
    invalid_unattached_indexes: list[str] = []
    if not context.as_sql:
        connection = op.get_bind()
        invalid_unattached_indexes = [
            index_name
            for index_name in _CANDIDATE_KEY_INDEXES
            if connection.execute(
                sa.text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                        FROM pg_catalog.pg_index AS index_state
                        LEFT JOIN pg_catalog.pg_constraint AS dependency
                          ON dependency.conindid = index_state.indexrelid
                       WHERE index_state.indexrelid =
                             to_regclass(:qualified_index_name)
                         AND index_state.indisvalid IS FALSE
                         AND dependency.oid IS NULL
                    )
                    """
                ),
                {"qualified_index_name": f'public."{index_name}"'},
            ).scalar_one()
        ]
    with context.autocommit_block():
        # A cancelled concurrent build leaves an invalid same-named index.
        # Remove only that unattached artifact so a retry can rebuild safely.
        for index_name in invalid_unattached_indexes:
            op.execute(f'DROP INDEX CONCURRENTLY public."{index_name}"')
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_appointment_id_doctor_patient "
            "ON public.appointment (id, doctor_id, patient_id)"
        )
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_practice_member_id_practice_doctor "
            "ON public.practice_member (id, practice_id, doctor_id)"
        )

    # A retry after a later failure may find either bare indexes or attached
    # constraints. Attach only the former; PostgreSQL then takes ownership of
    # each index and preserves its concurrency-safe build.
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'uq_appointment_id_doctor_patient'
               AND conrelid = 'public.appointment'::regclass
          ) THEN
            ALTER TABLE public.appointment
              ADD CONSTRAINT uq_appointment_id_doctor_patient
              UNIQUE USING INDEX uq_appointment_id_doctor_patient;
          END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'uq_practice_member_id_practice_doctor'
               AND conrelid = 'public.practice_member'::regclass
          ) THEN
            ALTER TABLE public.practice_member
              ADD CONSTRAINT uq_practice_member_id_practice_doctor
              UNIQUE USING INDEX uq_practice_member_id_practice_doctor;
          END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    _add_existing_table_candidate_keys()
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('visit_prep',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False, comment='TypeID with prefix: visit_prep'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('practice_id', activemodel.types.typeid.TypeIDType(prefix='practice'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('owner_doctor_id', activemodel.types.typeid.TypeIDType(prefix='doctor'), nullable=False),
    sa.Column('question_set_version_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_question_set_version'), nullable=False),
    sa.Column('patient_id', activemodel.types.typeid.TypeIDType(prefix='patient'), nullable=True),
    sa.Column('appointment_id', activemodel.types.typeid.TypeIDType(prefix='appointment'), nullable=True),
    sa.Column('creation_source', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('creation_idempotency_key', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('creation_payload_hmac_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('recipient_snapshot_ciphertext', sa.Text(), nullable=False),
    sa.Column('recipient_snapshot_format_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('has_email_contact', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('has_sms_contact', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('physician_display_name', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=False),
    sa.Column('practice_display_name', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
    sa.Column('lifecycle_state', sa.String(length=24), server_default=sa.text("'invited'"), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_by_role', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('misdirected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('misdirected_reason', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('misdirected_incident_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
    sa.Column('misdirected_disposition', sqlmodel.sql.sqltypes.AutoString(length=24), nullable=True),
    sa.Column('misdirected_disposition_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('misdirected_disposition_by_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
    sa.Column('misdirected_disposition_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
    sa.Column('revocation_reason', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('retention_policy_version', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('retain_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('patient_linked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('patient_linked_by_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
    sa.Column('appointment_linked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('appointment_linked_by_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
    sa.CheckConstraint("(confirmed_at IS NULL AND confirmed_by_role IS NULL) OR (confirmed_at IS NOT NULL AND confirmed_by_role IN ('patient', 'caregiver'))", name=op.f('visit_prep_confirmation_complete_check')),
    sa.CheckConstraint("(lifecycle_state = 'invited' AND confirmed_at IS NULL AND started_at IS NULL AND completed_at IS NULL AND misdirected_at IS NULL) OR (lifecycle_state = 'confirmed' AND confirmed_at IS NOT NULL AND started_at IS NULL AND completed_at IS NULL AND misdirected_at IS NULL) OR (lifecycle_state = 'in_progress' AND confirmed_at IS NOT NULL AND started_at IS NOT NULL AND completed_at IS NULL AND misdirected_at IS NULL) OR (lifecycle_state = 'completed' AND confirmed_at IS NOT NULL AND started_at IS NOT NULL AND completed_at IS NOT NULL AND misdirected_at IS NULL) OR (lifecycle_state = 'misdirected' AND confirmed_at IS NOT NULL AND misdirected_at IS NOT NULL)", name=op.f('visit_prep_lifecycle_timestamps_match_state_check')),
    sa.CheckConstraint("(misdirected_at IS NULL AND misdirected_reason IS NULL AND misdirected_incident_ref IS NULL AND misdirected_disposition IS NULL AND misdirected_disposition_at IS NULL AND misdirected_disposition_by_ref IS NULL AND misdirected_disposition_until IS NULL) OR (misdirected_at IS NOT NULL AND misdirected_reason IS NOT NULL AND misdirected_reason ~ '^[a-z][a-z0-9_]{0,63}$' AND misdirected_incident_ref IS NOT NULL AND misdirected_incident_ref = btrim(misdirected_incident_ref) AND char_length(misdirected_incident_ref) BETWEEN 1 AND 160 AND misdirected_disposition IS NOT NULL AND ((misdirected_disposition = 'pending_review' AND misdirected_disposition_at IS NULL AND misdirected_disposition_by_ref IS NULL AND misdirected_disposition_until IS NULL) OR (misdirected_disposition IN ('retain_until', 'purge_approved') AND misdirected_disposition_at IS NOT NULL AND misdirected_disposition_at >= misdirected_at AND misdirected_disposition_by_ref IS NOT NULL AND misdirected_disposition_by_ref = btrim(misdirected_disposition_by_ref) AND char_length(misdirected_disposition_by_ref) BETWEEN 1 AND 160 AND misdirected_disposition_until IS NOT NULL AND misdirected_disposition_until >= misdirected_disposition_at) OR (misdirected_disposition = 'legal_hold' AND misdirected_disposition_at IS NOT NULL AND misdirected_disposition_at >= misdirected_at AND misdirected_disposition_by_ref IS NOT NULL AND misdirected_disposition_by_ref = btrim(misdirected_disposition_by_ref) AND char_length(misdirected_disposition_by_ref) BETWEEN 1 AND 160 AND misdirected_disposition_until IS NULL)))", name=op.f('visit_prep_misdirection_complete_check')),
    sa.CheckConstraint("(revoked_at IS NULL AND revoked_by_ref IS NULL AND revocation_reason IS NULL) OR (revoked_at IS NOT NULL AND revoked_at >= created_at AND revoked_by_ref IS NOT NULL AND revoked_by_ref = btrim(revoked_by_ref) AND char_length(revoked_by_ref) BETWEEN 1 AND 160 AND revocation_reason IS NOT NULL AND revocation_reason ~ '^[a-z][a-z0-9_]{0,63}$')", name=op.f('visit_prep_revocation_complete_check')),
    sa.CheckConstraint("creation_idempotency_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'", name=op.f('visit_prep_creation_idempotency_key_shape_check')),
    sa.CheckConstraint("creation_payload_hmac_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_creation_payload_hmac_sha256_shape_check')),
    sa.CheckConstraint("creation_source ~ '^[a-z][a-z0-9_]{0,31}$'", name=op.f('visit_prep_creation_source_shape_check')),
    sa.CheckConstraint("lifecycle_state <> 'misdirected' OR revoked_at IS NOT NULL", name=op.f('visit_prep_misdirected_is_revoked_check')),
    sa.CheckConstraint("lifecycle_state IN ('invited', 'confirmed', 'in_progress', 'completed', 'misdirected')", name=op.f('visit_prep_lifecycle_state_allowed_check')),
    sa.CheckConstraint('(appointment_id IS NULL AND appointment_linked_at IS NULL AND appointment_linked_by_ref IS NULL) OR (appointment_id IS NOT NULL AND patient_id IS NOT NULL AND appointment_linked_at IS NOT NULL AND appointment_linked_at >= created_at AND appointment_linked_at >= patient_linked_at AND appointment_linked_by_ref IS NOT NULL AND appointment_linked_by_ref = btrim(appointment_linked_by_ref) AND char_length(appointment_linked_by_ref) BETWEEN 1 AND 160)', name=op.f('visit_prep_appointment_link_complete_check')),
    sa.CheckConstraint('(patient_id IS NULL AND patient_linked_at IS NULL AND patient_linked_by_ref IS NULL) OR (patient_id IS NOT NULL AND patient_linked_at IS NOT NULL AND patient_linked_at >= created_at AND patient_linked_by_ref IS NOT NULL AND patient_linked_by_ref = btrim(patient_linked_by_ref) AND char_length(patient_linked_by_ref) BETWEEN 1 AND 160)', name=op.f('visit_prep_patient_link_complete_check')),
    sa.CheckConstraint('(retention_policy_version IS NULL AND retain_until IS NULL) OR (confirmed_at IS NOT NULL AND retention_policy_version IS NOT NULL AND retention_policy_version = btrim(retention_policy_version) AND char_length(retention_policy_version) BETWEEN 1 AND 64 AND retain_until IS NOT NULL AND retain_until >= confirmed_at)', name=op.f('visit_prep_retention_snapshot_complete_check')),
    sa.CheckConstraint('completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)', name=op.f('visit_prep_completion_after_start_check')),
    sa.CheckConstraint('confirmed_at IS NULL OR (retention_policy_version IS NOT NULL AND retain_until IS NOT NULL)', name=op.f('visit_prep_confirmed_has_retention_snapshot_check')),
    sa.CheckConstraint('confirmed_at IS NULL OR confirmed_at >= created_at', name=op.f('visit_prep_confirmation_not_before_creation_check')),
    sa.CheckConstraint('has_email_contact IS TRUE OR has_sms_contact IS TRUE', name=op.f('visit_prep_recipient_has_delivery_contact_check')),
    sa.CheckConstraint('misdirected_at IS NULL OR (confirmed_at IS NOT NULL AND misdirected_at >= COALESCE(completed_at, started_at, confirmed_at) AND revoked_at IS NOT NULL AND revoked_at <= misdirected_at)', name=op.f('visit_prep_misdirection_after_interaction_check')),
    sa.CheckConstraint('octet_length(recipient_snapshot_ciphertext) BETWEEN 1 AND 16384', name=op.f('visit_prep_recipient_snapshot_ciphertext_size_check')),
    sa.CheckConstraint('physician_display_name = btrim(physician_display_name) AND char_length(physician_display_name) BETWEEN 1 AND 160', name=op.f('visit_prep_physician_display_name_normalized_check')),
    sa.CheckConstraint('practice_display_name = btrim(practice_display_name) AND char_length(practice_display_name) BETWEEN 1 AND 200', name=op.f('visit_prep_practice_display_name_normalized_check')),
    sa.CheckConstraint('recipient_snapshot_format_version = 1', name=op.f('visit_prep_recipient_snapshot_format_version_check')),
    sa.CheckConstraint('started_at IS NULL OR (confirmed_at IS NOT NULL AND started_at >= confirmed_at)', name=op.f('visit_prep_start_after_confirmation_check')),
    sa.ForeignKeyConstraint(['appointment_id', 'owner_doctor_id', 'patient_id'], ['appointment.id', 'appointment.doctor_id', 'appointment.patient_id'], name='fk_visit_prep_appointment_owner_patient', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['owner_practice_member_id', 'practice_id', 'owner_doctor_id'], ['practice_member.id', 'practice_member.practice_id', 'practice_member.doctor_id'], name='fk_visit_prep_owner_scope', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['patient_id', 'owner_doctor_id'], ['patient.id', 'patient.doctor_id'], name='fk_visit_prep_patient_owner', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['question_set_version_id', 'owner_practice_member_id'], ['visit_prep_question_set_version.id', 'visit_prep_question_set_version.owner_practice_member_id'], name='fk_visit_prep_question_version_owner', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_pkey')),
    sa.UniqueConstraint('id', 'owner_practice_member_id', name='uq_visit_prep_id_owner'),
    sa.UniqueConstraint('owner_practice_member_id', 'creation_idempotency_key', name='uq_visit_prep_owner_creation_key'),
    comment='One physician-owned asynchronous Visit Prep invitation and record.'
    )
    op.create_index('visit_prep_appointment_idx', 'visit_prep', ['appointment_id'], unique=False, postgresql_where=sa.text('appointment_id IS NOT NULL'))
    op.create_index('visit_prep_misdirected_disposition_until_idx', 'visit_prep', ['misdirected_disposition_until'], unique=False, postgresql_where=sa.text("lifecycle_state = 'misdirected' AND misdirected_disposition_until IS NOT NULL"))
    op.create_index('visit_prep_misdirected_idx', 'visit_prep', ['misdirected_at'], unique=False, postgresql_where=sa.text('misdirected_at IS NOT NULL'))
    op.create_index('visit_prep_owner_created_idx', 'visit_prep', ['owner_practice_member_id', 'created_at'], unique=False)
    op.create_index('visit_prep_patient_idx', 'visit_prep', ['patient_id'], unique=False, postgresql_where=sa.text('patient_id IS NOT NULL'))
    op.create_index('visit_prep_practice_created_idx', 'visit_prep', ['practice_id', 'created_at'], unique=False)
    op.create_index('visit_prep_retain_until_idx', 'visit_prep', ['retain_until'], unique=False, postgresql_where=sa.text('retain_until IS NOT NULL'))
    op.create_table('visit_prep_legal_hold',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_legal_hold'), nullable=False, comment='TypeID with prefix: visit_prep_legal_hold'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('visit_prep_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('placed_by_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=False),
    sa.Column('placement_reason', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_by_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
    sa.Column('release_reason', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.CheckConstraint("(released_at IS NULL AND released_by_ref IS NULL AND release_reason IS NULL) OR (released_at IS NOT NULL AND released_at >= placed_at AND released_by_ref IS NOT NULL AND released_by_ref = btrim(released_by_ref) AND char_length(released_by_ref) BETWEEN 1 AND 160 AND release_reason IS NOT NULL AND release_reason ~ '^[a-z][a-z0-9_]{0,63}$')", name=op.f('visit_prep_legal_hold_release_complete_check')),
    sa.CheckConstraint("placement_reason ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f('visit_prep_legal_hold_placement_reason_shape_check')),
    sa.CheckConstraint('generation > 0', name=op.f('visit_prep_legal_hold_generation_positive_check')),
    sa.CheckConstraint('placed_by_ref = btrim(placed_by_ref) AND char_length(placed_by_ref) BETWEEN 1 AND 160', name=op.f('visit_prep_legal_hold_placed_by_ref_normalized_check')),
    sa.ForeignKeyConstraint(['visit_prep_id', 'owner_practice_member_id'], ['visit_prep.id', 'visit_prep.owner_practice_member_id'], name='fk_visit_prep_legal_hold_owner', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_legal_hold_pkey')),
    sa.UniqueConstraint('visit_prep_id', 'generation', name='uq_visit_prep_legal_hold_generation'),
    comment='Append-only evidence for a legal hold and its optional later release.'
    )
    op.create_index('uq_visit_prep_active_legal_hold', 'visit_prep_legal_hold', ['visit_prep_id'], unique=True, postgresql_where=sa.text('released_at IS NULL'))
    op.create_table('visit_prep_schedule_generation',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_schedule'), nullable=False, comment='TypeID with prefix: visit_prep_schedule'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('visit_prep_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('timezone_name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('unused_purge_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('unused_hard_purge_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("timezone_name = btrim(timezone_name) AND char_length(timezone_name) BETWEEN 1 AND 64 AND timezone_name ~ '^[A-Za-z][A-Za-z0-9._+-]*(/[A-Za-z0-9._+-]+)*$'", name=op.f('visit_prep_schedule_generation_timezone_name_shape_check')),
    sa.CheckConstraint("unused_hard_purge_at = scheduled_at + interval '2160 hours'", name=op.f('visit_prep_schedule_generation_unused_hard_purge_at_policy_check')),
    sa.CheckConstraint("unused_purge_at = scheduled_at + interval '720 hours'", name=op.f('visit_prep_schedule_generation_unused_purge_at_policy_check')),
    sa.CheckConstraint('generation > 0', name=op.f('visit_prep_schedule_generation_generation_positive_check')),
    sa.ForeignKeyConstraint(['visit_prep_id', 'owner_practice_member_id'], ['visit_prep.id', 'visit_prep.owner_practice_member_id'], name='fk_visit_prep_schedule_owner', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_schedule_generation_pkey')),
    sa.UniqueConstraint('id', 'visit_prep_id', 'owner_practice_member_id', name='uq_visit_prep_schedule_id_prep_owner'),
    sa.UniqueConstraint('visit_prep_id', 'generation', name='uq_visit_prep_schedule_generation'),
    comment='Immutable schedule history; the greatest generation is current.'
    )
    op.create_index('visit_prep_schedule_unused_hard_purge_idx', 'visit_prep_schedule_generation', ['unused_hard_purge_at'], unique=False)
    op.create_index('visit_prep_schedule_unused_purge_idx', 'visit_prep_schedule_generation', ['unused_purge_at'], unique=False)
    op.create_table('visit_prep_credential_generation',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_credential'), nullable=False, comment='TypeID with prefix: visit_prep_credential'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('visit_prep_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('schedule_generation_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_schedule'), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('token_format_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by_ref', sqlmodel.sql.sqltypes.AutoString(length=160), nullable=True),
    sa.Column('revocation_reason', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.CheckConstraint("(revoked_at IS NULL AND revoked_by_ref IS NULL AND revocation_reason IS NULL) OR (revoked_at IS NOT NULL AND revoked_at >= issued_at AND revoked_by_ref IS NOT NULL AND revoked_by_ref = btrim(revoked_by_ref) AND char_length(revoked_by_ref) BETWEEN 1 AND 160 AND revocation_reason IS NOT NULL AND revocation_reason ~ '^[a-z][a-z0-9_]{0,63}$')", name=op.f('visit_prep_credential_generation_revocation_complete_check')),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_credential_generation_token_hash_shape_check')),
    sa.CheckConstraint('expires_at > issued_at', name=op.f('visit_prep_credential_generation_expiry_after_issue_check')),
    sa.CheckConstraint('generation > 0', name=op.f('visit_prep_credential_generation_generation_positive_check')),
    sa.CheckConstraint('token_format_version = 1', name=op.f('visit_prep_credential_generation_token_format_version_check')),
    sa.ForeignKeyConstraint(['schedule_generation_id', 'visit_prep_id', 'owner_practice_member_id'], ['visit_prep_schedule_generation.id', 'visit_prep_schedule_generation.visit_prep_id', 'visit_prep_schedule_generation.owner_practice_member_id'], name='fk_visit_prep_credential_schedule_owner', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_credential_generation_pkey')),
    sa.UniqueConstraint('token_hash', name='uq_visit_prep_credential_token_hash'),
    sa.UniqueConstraint('visit_prep_id', 'generation', name='uq_visit_prep_credential_generation'),
    comment='One verifier-only bearer generation for an exact schedule snapshot.'
    )
    op.create_index('uq_visit_prep_active_credential', 'visit_prep_credential_generation', ['visit_prep_id'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index('visit_prep_credential_expiry_idx', 'visit_prep_credential_generation', ['expires_at'], unique=False)
    op.create_index('visit_prep_credential_schedule_idx', 'visit_prep_credential_generation', ['schedule_generation_id'], unique=False)
    op.execute(
        """
        CREATE FUNCTION public.cosmas_require_visit_prep_published_version()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
          IF NEW.lifecycle_state <> 'invited'
             OR NEW.confirmed_at IS NOT NULL
             OR NEW.started_at IS NOT NULL
             OR NEW.completed_at IS NOT NULL
             OR NEW.misdirected_at IS NOT NULL
             OR NEW.revoked_at IS NOT NULL
             OR NEW.retention_policy_version IS NOT NULL
             OR NEW.retain_until IS NOT NULL THEN
            RAISE EXCEPTION 'Visit Prep must be created in the invited state'
              USING ERRCODE = '55000';
          END IF;

          PERFORM 1
            FROM public.visit_prep_question_set_version
           WHERE id = NEW.question_set_version_id
             AND owner_practice_member_id = NEW.owner_practice_member_id
             AND published_at IS NOT NULL
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep requires a published owned question version'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER visit_prep_require_published_version
        BEFORE INSERT ON public.visit_prep
        FOR EACH ROW
        EXECUTE FUNCTION public.cosmas_require_visit_prep_published_version()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.cosmas_guard_visit_prep_mutation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
          current_unused_purge_at timestamptz;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            PERFORM 1
              FROM public.visit_prep_legal_hold
             WHERE visit_prep_id = OLD.id
               AND released_at IS NULL
             FOR KEY SHARE;
            IF FOUND THEN
              RAISE EXCEPTION 'Visit Prep with an active legal hold cannot be deleted'
                USING ERRCODE = '55000';
            END IF;

            IF OLD.lifecycle_state = 'misdirected' THEN
              IF OLD.misdirected_disposition IN ('pending_review', 'legal_hold')
                 OR OLD.misdirected_disposition_until IS NULL
                 OR OLD.misdirected_disposition_until > now() THEN
                RAISE EXCEPTION 'quarantined Visit Prep is not eligible for deletion'
                  USING ERRCODE = '55000';
              END IF;
            ELSIF OLD.confirmed_at IS NOT NULL THEN
              IF OLD.retain_until IS NULL OR OLD.retain_until > now() THEN
                RAISE EXCEPTION 'clinical Visit Prep is still retained'
                  USING ERRCODE = '55000';
              END IF;
            ELSIF OLD.revocation_reason IS NULL
               OR OLD.revocation_reason NOT IN (
                 'wrong_recipient',
                 'appointment_cancelled'
               ) THEN
              SELECT unused_purge_at
                INTO current_unused_purge_at
                FROM public.visit_prep_schedule_generation
               WHERE visit_prep_id = OLD.id
               ORDER BY generation DESC
               LIMIT 1
               FOR KEY SHARE;
              IF current_unused_purge_at IS NULL
                 OR current_unused_purge_at > now() THEN
                RAISE EXCEPTION 'unused Visit Prep is not yet eligible for deletion'
                  USING ERRCODE = '55000';
              END IF;
            END IF;
            RETURN OLD;
          END IF;

          IF ROW(
               NEW.id,
               NEW.created_at,
               NEW.practice_id,
               NEW.owner_practice_member_id,
               NEW.owner_doctor_id,
               NEW.question_set_version_id,
               NEW.creation_source,
               NEW.creation_idempotency_key,
               NEW.creation_payload_hmac_sha256,
               NEW.recipient_snapshot_ciphertext,
               NEW.recipient_snapshot_format_version,
               NEW.has_email_contact,
               NEW.has_sms_contact,
               NEW.physician_display_name,
               NEW.practice_display_name
             ) IS DISTINCT FROM ROW(
               OLD.id,
               OLD.created_at,
               OLD.practice_id,
               OLD.owner_practice_member_id,
               OLD.owner_doctor_id,
               OLD.question_set_version_id,
               OLD.creation_source,
               OLD.creation_idempotency_key,
               OLD.creation_payload_hmac_sha256,
               OLD.recipient_snapshot_ciphertext,
               OLD.recipient_snapshot_format_version,
               OLD.has_email_contact,
               OLD.has_sms_contact,
               OLD.physician_display_name,
               OLD.practice_display_name
             ) THEN
            RAISE EXCEPTION 'Visit Prep ownership and creation snapshots are immutable'
              USING ERRCODE = '55000';
          END IF;

          IF OLD.patient_id IS NOT NULL
             AND ROW(
               NEW.patient_id,
               NEW.patient_linked_at,
               NEW.patient_linked_by_ref
             ) IS DISTINCT FROM ROW(
               OLD.patient_id,
               OLD.patient_linked_at,
               OLD.patient_linked_by_ref
             ) THEN
            RAISE EXCEPTION 'Visit Prep patient linkage is immutable once attached'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.appointment_id IS NOT NULL
             AND ROW(
               NEW.appointment_id,
               NEW.appointment_linked_at,
               NEW.appointment_linked_by_ref
             ) IS DISTINCT FROM ROW(
               OLD.appointment_id,
               OLD.appointment_linked_at,
               OLD.appointment_linked_by_ref
             ) THEN
            RAISE EXCEPTION 'Visit Prep appointment linkage is immutable once attached'
              USING ERRCODE = '55000';
          END IF;
          IF (
               OLD.revoked_at IS NOT NULL
               OR NEW.revoked_at IS NOT NULL
               OR OLD.lifecycle_state = 'misdirected'
               OR NEW.lifecycle_state = 'misdirected'
             ) AND (
               NEW.patient_id IS DISTINCT FROM OLD.patient_id
               OR NEW.appointment_id IS DISTINCT FROM OLD.appointment_id
             ) THEN
            RAISE EXCEPTION 'revoked or quarantined Visit Prep cannot gain canonical links'
              USING ERRCODE = '55000';
          END IF;

          IF NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state
             AND NOT (
               (OLD.lifecycle_state = 'invited' AND NEW.lifecycle_state = 'confirmed')
               OR (
                 OLD.lifecycle_state = 'confirmed'
                 AND NEW.lifecycle_state IN ('in_progress', 'misdirected')
               )
               OR (
                 OLD.lifecycle_state = 'in_progress'
                 AND NEW.lifecycle_state IN ('completed', 'misdirected')
               )
               OR (
                 OLD.lifecycle_state = 'completed'
                 AND NEW.lifecycle_state = 'misdirected'
               )
             ) THEN
            RAISE EXCEPTION 'invalid Visit Prep lifecycle transition'
              USING ERRCODE = '55000';
          END IF;
          IF (OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NOT NULL)
             AND NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state
             AND NEW.lifecycle_state <> 'misdirected' THEN
            RAISE EXCEPTION 'revoked Visit Prep cannot advance'
              USING ERRCODE = '55000';
          END IF;

          IF OLD.confirmed_at IS NOT NULL
             AND ROW(NEW.confirmed_at, NEW.confirmed_by_role)
                 IS DISTINCT FROM ROW(OLD.confirmed_at, OLD.confirmed_by_role) THEN
            RAISE EXCEPTION 'Visit Prep confirmation is immutable'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.started_at IS NOT NULL
             AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
            RAISE EXCEPTION 'Visit Prep start is immutable'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.completed_at IS NOT NULL
             AND NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
            RAISE EXCEPTION 'Visit Prep completion is immutable'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.retention_policy_version IS NOT NULL
             AND ROW(NEW.retention_policy_version, NEW.retain_until)
                 IS DISTINCT FROM ROW(
                   OLD.retention_policy_version,
                   OLD.retain_until
                 ) THEN
            RAISE EXCEPTION 'Visit Prep retention snapshot is immutable'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.revoked_at IS NOT NULL
             AND ROW(NEW.revoked_at, NEW.revoked_by_ref, NEW.revocation_reason)
                 IS DISTINCT FROM ROW(
                   OLD.revoked_at,
                   OLD.revoked_by_ref,
                   OLD.revocation_reason
                 ) THEN
            RAISE EXCEPTION 'Visit Prep revocation is irreversible'
              USING ERRCODE = '55000';
          END IF;

          IF OLD.misdirected_at IS NULL
             AND NEW.misdirected_at IS NOT NULL
             AND NEW.misdirected_disposition IS DISTINCT FROM 'pending_review' THEN
            RAISE EXCEPTION 'Visit Prep quarantine requires pending review first'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.misdirected_at IS NOT NULL THEN
            IF ROW(
                 NEW.misdirected_at,
                 NEW.misdirected_reason,
                 NEW.misdirected_incident_ref
               ) IS DISTINCT FROM ROW(
                 OLD.misdirected_at,
                 OLD.misdirected_reason,
                 OLD.misdirected_incident_ref
               ) THEN
              RAISE EXCEPTION 'Visit Prep quarantine evidence is immutable'
                USING ERRCODE = '55000';
            END IF;

            IF NEW.misdirected_disposition IS DISTINCT FROM
               OLD.misdirected_disposition THEN
              IF NEW.misdirected_disposition IS NULL OR NOT (
                (
                  OLD.misdirected_disposition = 'pending_review'
                  AND NEW.misdirected_disposition IN (
                    'retain_until',
                    'purge_approved',
                    'legal_hold'
                  )
                )
                OR (
                  OLD.misdirected_disposition = 'legal_hold'
                  AND NEW.misdirected_disposition IN (
                    'retain_until',
                    'purge_approved'
                  )
                )
              ) THEN
                RAISE EXCEPTION 'invalid Visit Prep quarantine disposition transition'
                  USING ERRCODE = '55000';
              END IF;

              IF OLD.misdirected_disposition = 'legal_hold' THEN
                PERFORM 1
                  FROM public.visit_prep_legal_hold
                 WHERE visit_prep_id = OLD.id
                   AND released_at IS NULL
                 FOR KEY SHARE;
                IF FOUND THEN
                  RAISE EXCEPTION 'release the active legal hold before disposition'
                    USING ERRCODE = '55000';
                END IF;
              END IF;
            ELSIF ROW(
               NEW.misdirected_disposition_at,
               NEW.misdirected_disposition_by_ref,
               NEW.misdirected_disposition_until
             ) IS DISTINCT FROM ROW(
               OLD.misdirected_disposition_at,
               OLD.misdirected_disposition_by_ref,
               OLD.misdirected_disposition_until
             ) THEN
              RAISE EXCEPTION 'Visit Prep quarantine disposition is immutable'
                USING ERRCODE = '55000';
            END IF;
          END IF;

          IF NEW.misdirected_disposition = 'legal_hold' THEN
            PERFORM 1
              FROM public.visit_prep_legal_hold
             WHERE visit_prep_id = NEW.id
               AND released_at IS NULL
             FOR KEY SHARE;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'legal-hold disposition requires an active hold'
                USING ERRCODE = '55000';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER visit_prep_guard_mutation
        BEFORE UPDATE OR DELETE ON public.visit_prep
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.cosmas_guard_visit_prep_schedule()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
          root_state text;
          root_revoked_at timestamptz;
          expected_generation integer;
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'Visit Prep schedule generations are immutable'
              USING ERRCODE = '55000';
          END IF;
          IF TG_OP = 'DELETE' THEN
            PERFORM 1 FROM public.visit_prep WHERE id = OLD.visit_prep_id;
            IF FOUND THEN
              RAISE EXCEPTION 'Visit Prep schedule must be deleted through aggregate cleanup'
                USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;

          SELECT lifecycle_state, revoked_at
            INTO root_state, root_revoked_at
            FROM public.visit_prep
           WHERE id = NEW.visit_prep_id
             AND owner_practice_member_id = NEW.owner_practice_member_id
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep schedule owner does not exist'
              USING ERRCODE = '23503';
          END IF;
          IF root_revoked_at IS NOT NULL
             OR root_state IN ('completed', 'misdirected') THEN
            RAISE EXCEPTION 'inactive Visit Prep cannot be rescheduled'
              USING ERRCODE = '55000';
          END IF;

          SELECT COALESCE(MAX(generation), 0) + 1
            INTO expected_generation
            FROM public.visit_prep_schedule_generation
           WHERE visit_prep_id = NEW.visit_prep_id;
          IF NEW.generation <> expected_generation THEN
            RAISE EXCEPTION 'Visit Prep schedule generation is not sequential'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER visit_prep_schedule_guard_mutation
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_schedule_generation
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_schedule()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.cosmas_guard_visit_prep_credential()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
          root_state text;
          root_revoked_at timestamptz;
          current_schedule_id uuid;
          current_scheduled_at timestamptz;
          expected_generation integer;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            PERFORM 1 FROM public.visit_prep WHERE id = OLD.visit_prep_id;
            IF FOUND THEN
              RAISE EXCEPTION 'Visit Prep credential must be deleted through aggregate cleanup'
                USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;
          IF TG_OP = 'UPDATE' THEN
            IF ROW(
                 NEW.id,
                 NEW.created_at,
                 NEW.visit_prep_id,
                 NEW.owner_practice_member_id,
                 NEW.schedule_generation_id,
                 NEW.generation,
                 NEW.token_format_version,
                 NEW.token_hash,
                 NEW.issued_at,
                 NEW.expires_at
               ) IS DISTINCT FROM ROW(
                 OLD.id,
                 OLD.created_at,
                 OLD.visit_prep_id,
                 OLD.owner_practice_member_id,
                 OLD.schedule_generation_id,
                 OLD.generation,
                 OLD.token_format_version,
                 OLD.token_hash,
                 OLD.issued_at,
                 OLD.expires_at
               ) THEN
              RAISE EXCEPTION 'Visit Prep credential lineage is immutable'
                USING ERRCODE = '55000';
            END IF;
            IF OLD.revoked_at IS NOT NULL
               AND ROW(NEW.revoked_at, NEW.revoked_by_ref, NEW.revocation_reason)
                   IS DISTINCT FROM ROW(
                     OLD.revoked_at,
                     OLD.revoked_by_ref,
                     OLD.revocation_reason
                   ) THEN
              RAISE EXCEPTION 'Visit Prep credential revocation is irreversible'
                USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
          END IF;

          SELECT lifecycle_state, revoked_at
            INTO root_state, root_revoked_at
            FROM public.visit_prep
           WHERE id = NEW.visit_prep_id
             AND owner_practice_member_id = NEW.owner_practice_member_id
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep credential owner does not exist'
              USING ERRCODE = '23503';
          END IF;
          IF root_revoked_at IS NOT NULL
             OR root_state IN ('completed', 'misdirected') THEN
            RAISE EXCEPTION 'inactive Visit Prep cannot receive a credential'
              USING ERRCODE = '55000';
          END IF;

          SELECT id, scheduled_at
            INTO current_schedule_id, current_scheduled_at
            FROM public.visit_prep_schedule_generation
           WHERE visit_prep_id = NEW.visit_prep_id
             AND owner_practice_member_id = NEW.owner_practice_member_id
           ORDER BY generation DESC
           LIMIT 1
           FOR KEY SHARE;
          IF current_schedule_id IS NULL
             OR NEW.schedule_generation_id <> current_schedule_id THEN
            RAISE EXCEPTION 'Visit Prep credential requires the current schedule'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.expires_at <> current_scheduled_at + interval '72 hours' THEN
            RAISE EXCEPTION 'Visit Prep credential expiry must be schedule plus 72 hours'
              USING ERRCODE = '55000';
          END IF;

          SELECT COALESCE(MAX(generation), 0) + 1
            INTO expected_generation
            FROM public.visit_prep_credential_generation
           WHERE visit_prep_id = NEW.visit_prep_id;
          IF NEW.generation <> expected_generation THEN
            RAISE EXCEPTION 'Visit Prep credential generation is not sequential'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER visit_prep_credential_guard_mutation
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_credential_generation
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_credential()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.cosmas_guard_visit_prep_legal_hold()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
          root_created_at timestamptz;
          expected_generation integer;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            PERFORM 1 FROM public.visit_prep WHERE id = OLD.visit_prep_id;
            IF FOUND THEN
              RAISE EXCEPTION 'Visit Prep legal hold must be deleted through aggregate cleanup'
                USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;
          IF TG_OP = 'UPDATE' THEN
            IF ROW(
                 NEW.id,
                 NEW.created_at,
                 NEW.visit_prep_id,
                 NEW.owner_practice_member_id,
                 NEW.generation,
                 NEW.placed_at,
                 NEW.placed_by_ref,
                 NEW.placement_reason
               ) IS DISTINCT FROM ROW(
                 OLD.id,
                 OLD.created_at,
                 OLD.visit_prep_id,
                 OLD.owner_practice_member_id,
                 OLD.generation,
                 OLD.placed_at,
                 OLD.placed_by_ref,
                 OLD.placement_reason
               ) THEN
              RAISE EXCEPTION 'Visit Prep legal-hold placement is immutable'
                USING ERRCODE = '55000';
            END IF;
            IF OLD.released_at IS NOT NULL
               AND ROW(NEW.released_at, NEW.released_by_ref, NEW.release_reason)
                   IS DISTINCT FROM ROW(
                     OLD.released_at,
                     OLD.released_by_ref,
                     OLD.release_reason
                   ) THEN
              RAISE EXCEPTION 'Visit Prep legal-hold release is irreversible'
                USING ERRCODE = '55000';
            END IF;
            IF OLD.released_at IS NULL
               AND NEW.released_at IS NOT NULL
               AND NEW.released_at > transaction_timestamp() THEN
              RAISE EXCEPTION 'Visit Prep legal-hold release cannot be future-dated'
                USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
          END IF;

          SELECT created_at
            INTO root_created_at
            FROM public.visit_prep
           WHERE id = NEW.visit_prep_id
             AND owner_practice_member_id = NEW.owner_practice_member_id
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep legal-hold owner does not exist'
              USING ERRCODE = '23503';
          END IF;
          IF NEW.placed_at < root_created_at THEN
            RAISE EXCEPTION 'Visit Prep legal hold predates its aggregate'
              USING ERRCODE = '23514';
          END IF;
          IF NEW.released_at IS NOT NULL THEN
            RAISE EXCEPTION 'Visit Prep legal hold must be active when created'
              USING ERRCODE = '55000';
          END IF;

          SELECT COALESCE(MAX(generation), 0) + 1
            INTO expected_generation
            FROM public.visit_prep_legal_hold
           WHERE visit_prep_id = NEW.visit_prep_id;
          IF NEW.generation <> expected_generation THEN
            RAISE EXCEPTION 'Visit Prep legal-hold generation is not sequential'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER visit_prep_legal_hold_guard_mutation
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_legal_hold
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_legal_hold()
        """
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    _refuse_populated_or_offline_downgrade()
    # ### commands auto generated by Alembic - please adjust! ###
    op.execute(
        "DROP TRIGGER IF EXISTS visit_prep_credential_guard_mutation "
        "ON public.visit_prep_credential_generation"
    )
    op.execute("DROP FUNCTION IF EXISTS public.cosmas_guard_visit_prep_credential()")
    op.execute(
        "DROP TRIGGER IF EXISTS visit_prep_schedule_guard_mutation "
        "ON public.visit_prep_schedule_generation"
    )
    op.execute("DROP FUNCTION IF EXISTS public.cosmas_guard_visit_prep_schedule()")
    op.execute(
        "DROP TRIGGER IF EXISTS visit_prep_legal_hold_guard_mutation "
        "ON public.visit_prep_legal_hold"
    )
    op.execute("DROP FUNCTION IF EXISTS public.cosmas_guard_visit_prep_legal_hold()")
    op.execute(
        "DROP TRIGGER IF EXISTS visit_prep_guard_mutation ON public.visit_prep"
    )
    op.execute("DROP FUNCTION IF EXISTS public.cosmas_guard_visit_prep_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS visit_prep_require_published_version "
        "ON public.visit_prep"
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS public.cosmas_require_visit_prep_published_version()
        """
    )
    op.drop_index(
        "visit_prep_credential_schedule_idx",
        table_name="visit_prep_credential_generation",
        schema="public",
    )
    op.drop_index(
        "visit_prep_credential_expiry_idx",
        table_name="visit_prep_credential_generation",
        schema="public",
    )
    op.drop_index(
        "uq_visit_prep_active_credential",
        table_name="visit_prep_credential_generation",
        schema="public",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("visit_prep_credential_generation", schema="public")
    op.drop_index(
        "visit_prep_schedule_unused_purge_idx",
        table_name="visit_prep_schedule_generation",
        schema="public",
    )
    op.drop_index(
        "visit_prep_schedule_unused_hard_purge_idx",
        table_name="visit_prep_schedule_generation",
        schema="public",
    )
    op.drop_table("visit_prep_schedule_generation", schema="public")
    op.drop_index(
        "uq_visit_prep_active_legal_hold",
        table_name="visit_prep_legal_hold",
        schema="public",
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.drop_table("visit_prep_legal_hold", schema="public")
    op.drop_index(
        "visit_prep_retain_until_idx",
        table_name="visit_prep",
        schema="public",
        postgresql_where=sa.text("retain_until IS NOT NULL"),
    )
    op.drop_index(
        "visit_prep_practice_created_idx",
        table_name="visit_prep",
        schema="public",
    )
    op.drop_index(
        "visit_prep_patient_idx",
        table_name="visit_prep",
        schema="public",
        postgresql_where=sa.text("patient_id IS NOT NULL"),
    )
    op.drop_index(
        "visit_prep_owner_created_idx",
        table_name="visit_prep",
        schema="public",
    )
    op.drop_index(
        "visit_prep_misdirected_idx",
        table_name="visit_prep",
        schema="public",
        postgresql_where=sa.text("misdirected_at IS NOT NULL"),
    )
    op.drop_index(
        "visit_prep_misdirected_disposition_until_idx",
        table_name="visit_prep",
        schema="public",
        postgresql_where=sa.text(
            "lifecycle_state = 'misdirected' "
            "AND misdirected_disposition_until IS NOT NULL"
        ),
    )
    op.drop_index(
        "visit_prep_appointment_idx",
        table_name="visit_prep",
        schema="public",
        postgresql_where=sa.text("appointment_id IS NOT NULL"),
    )
    op.drop_table("visit_prep", schema="public")
    op.drop_constraint(
        "uq_practice_member_id_practice_doctor",
        "practice_member",
        type_="unique",
        schema="public",
    )
    op.drop_constraint(
        "uq_appointment_id_doctor_patient",
        "appointment",
        type_="unique",
        schema="public",
    )
    # ### end Alembic commands ###
