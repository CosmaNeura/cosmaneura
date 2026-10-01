"""Expand the single usage ledger with an explicit, chart-free batch origin.

Revision ID: 54bedaad381a
Revises: 2f87498bb9fa
Create Date: 2026-10-01 22:26:32.209643

Generated against the owned graph; unrelated Stripe/Scribe comment drift removed.
Old writes default to agent_turn and retain all required authority lineage.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

revision: str = '54bedaad381a'
down_revision: Union[str, None] = '2f87498bb9fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS = """(event_origin = 'agent_turn' AND status IN
('succeeded', 'failed', 'cancelled')) OR
(event_origin = 'visit_prep_batch' AND
((batch_phase = 'intent' AND status = 'started') OR
(batch_phase = 'result' AND status IN ('succeeded', 'failed'))))"""
_SURFACE = """(event_origin = 'agent_turn' AND surface = 'provider_assistant') OR
(event_origin = 'visit_prep_batch' AND surface = 'provider_dashboard')"""
_SHAPE = """
(event_origin = 'agent_turn' AND interaction_id IS NOT NULL AND
 tenant_id IS NOT NULL AND actor_id IS NOT NULL AND person_id IS NOT NULL AND
 batch_phase IS NULL AND call_intent_id IS NULL AND core_practice_id IS NULL AND
 core_doctor_id IS NULL AND core_member_id IS NULL AND preparation_ref IS NULL AND
 build_ref IS NULL AND build_epoch IS NULL AND build_attempt IS NULL AND
 source_version IS NULL AND build_lease_until IS NULL) OR
(event_origin = 'visit_prep_batch' AND interaction_id IS NULL AND
 tenant_id IS NULL AND actor_id IS NULL AND person_id IS NULL AND
 batch_phase IS NOT NULL AND batch_phase IN ('intent', 'result') AND
 core_practice_id IS NOT NULL AND core_doctor_id IS NOT NULL AND
 core_member_id IS NOT NULL AND preparation_ref IS NOT NULL AND build_ref IS NOT NULL AND
 build_epoch IS NOT NULL AND build_epoch > 0 AND build_attempt IS NOT NULL AND
 build_attempt BETWEEN 1 AND 3 AND source_version IS NOT NULL AND
 length(source_version) = 64 AND build_lease_until IS NOT NULL AND
 call_sequence = build_attempt AND workload_client_id = 'core_visit_prep' AND
 workstream = 'visit_prep' AND purpose = 'previsit_synthesis' AND
 call_kind = 'drafting' AND provider = 'azure_openai' AND model IS NOT NULL AND
 started_at IS NOT NULL AND
 ((batch_phase = 'intent' AND call_intent_id IS NULL AND completed_at IS NULL AND
   latency_ms IS NULL AND provider_request_id IS NULL AND
   usage_availability = 'unavailable' AND usage_source = 'not_available' AND
   input_tokens IS NULL AND output_tokens IS NULL AND cached_input_tokens IS NULL AND
   reasoning_tokens IS NULL) OR
  (batch_phase = 'result' AND call_intent_id IS NOT NULL AND completed_at IS NOT NULL AND
   completed_at >= started_at AND latency_ms IS NOT NULL AND cached_input_tokens IS NULL AND
   reasoning_tokens IS NULL AND
   ((usage_availability = 'complete' AND usage_source = 'provider_response' AND
     input_tokens IS NOT NULL AND output_tokens IS NOT NULL) OR
    (usage_availability = 'unavailable' AND usage_source = 'not_available' AND
     input_tokens IS NULL AND output_tokens IS NULL)))))
"""


def upgrade() -> None:
    op.add_column('agent_model_usage_event', sa.Column('event_origin', sa.String(length=32), server_default='agent_turn', nullable=False))
    for name, prefix in (
        ('core_practice_id', 'practice'), ('core_doctor_id', 'doctor'),
        ('core_member_id', 'practice_member'), ('preparation_ref', 'visit_prep'),
        ('build_ref', 'visit_prep_build'), ('call_intent_id', 'model_usage'),
    ):
        op.add_column('agent_model_usage_event', sa.Column(name, activemodel.types.typeid.TypeIDType(prefix=prefix), nullable=True))
    for name in ('build_epoch', 'build_attempt'):
        op.add_column('agent_model_usage_event', sa.Column(name, sa.Integer(), nullable=True))
    op.add_column('agent_model_usage_event', sa.Column('build_lease_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('agent_model_usage_event', sa.Column('source_version', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
    op.add_column('agent_model_usage_event', sa.Column('batch_phase', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=True))
    for column in ('interaction_id', 'tenant_id', 'actor_id', 'person_id'):
        op.alter_column('agent_model_usage_event', column, existing_type=sa.UUID(), nullable=True)
    for name, condition in (
        ('ck_agent_model_usage_event_status', _STATUS),
        ('ck_agent_model_usage_event_provider_surface', _SURFACE),
    ):
        op.drop_constraint(name, 'agent_model_usage_event', type_='check')
        op.create_check_constraint(name, 'agent_model_usage_event', condition)
    op.create_check_constraint('ck_model_usage_origin_shape', 'agent_model_usage_event', _SHAPE)
    op.create_unique_constraint('uq_model_usage_batch_phase', 'agent_model_usage_event', ['build_ref', 'build_epoch', 'build_attempt', 'batch_phase'])
    op.create_unique_constraint('uq_model_usage_intent_result', 'agent_model_usage_event', ['call_intent_id'])
    op.create_foreign_key(op.f('agent_model_usage_event_call_intent_id_fkey'), 'agent_model_usage_event', 'agent_model_usage_event', ['call_intent_id'], ['id'])
    op.execute("""
    CREATE FUNCTION visit_prep_usage_insert_guard() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE original public.agent_model_usage_event%ROWTYPE;
    DECLARE job public.visit_prep_synthesis_build%ROWTYPE;
    DECLARE prep public.visit_prep%ROWTYPE;
    BEGIN
      IF NEW.event_origin <> 'visit_prep_batch' THEN RETURN NEW; END IF;
      IF NEW.source_version !~ '^[0-9a-f]{64}$' OR
         NEW.request_digest !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid batch usage' USING ERRCODE = '23514';
      END IF;
      IF NEW.batch_phase = 'intent' THEN
        SELECT * INTO job FROM public.visit_prep_synthesis_build
          WHERE id = NEW.build_ref FOR UPDATE;
        SELECT * INTO prep FROM public.visit_prep WHERE id = NEW.preparation_ref;
        IF job.id IS NULL OR prep.id IS NULL OR job.visit_prep_id <> prep.id OR
           job.owner_practice_member_id <> NEW.core_member_id OR
           prep.owner_practice_member_id <> NEW.core_member_id OR
           prep.owner_doctor_id <> NEW.core_doctor_id OR prep.practice_id <> NEW.core_practice_id OR
           job.epoch <> NEW.build_epoch OR job.attempts <> NEW.build_attempt OR
           job.source_version <> NEW.source_version OR job.model_profile <> NEW.model OR
           job.state <> 'running' OR job.lease_until IS DISTINCT FROM NEW.build_lease_until OR
           clock_timestamp() >= job.lease_until THEN
          RAISE EXCEPTION 'Invalid batch usage' USING ERRCODE = '23514';
        END IF;
      ELSIF NEW.batch_phase = 'result' THEN
        SELECT * INTO original FROM public.agent_model_usage_event
          WHERE id = NEW.call_intent_id FOR KEY SHARE;
        IF original.id IS NULL OR original.event_origin <> 'visit_prep_batch' OR
           original.batch_phase <> 'intent' OR NEW.started_at < original.started_at OR
           ROW(NEW.core_practice_id, NEW.core_doctor_id, NEW.core_member_id,
               NEW.preparation_ref, NEW.build_ref, NEW.build_epoch, NEW.build_attempt,
               NEW.source_version, NEW.build_lease_until, NEW.model)
           IS DISTINCT FROM
           ROW(original.core_practice_id, original.core_doctor_id, original.core_member_id,
               original.preparation_ref, original.build_ref, original.build_epoch,
               original.build_attempt, original.source_version, original.build_lease_until,
               original.model) THEN
          RAISE EXCEPTION 'Invalid batch usage' USING ERRCODE = '23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER visit_prep_usage_insert_guard BEFORE INSERT ON public.agent_model_usage_event
      FOR EACH ROW EXECUTE FUNCTION visit_prep_usage_insert_guard();
    CREATE FUNCTION visit_prep_usage_truncate_guard() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM public.agent_model_usage_event
                 WHERE event_origin = 'visit_prep_batch') THEN
        RAISE EXCEPTION 'Batch usage is append-only' USING ERRCODE = '23514';
      END IF;
      RETURN NULL;
    END $$;
    CREATE TRIGGER visit_prep_usage_truncate_guard BEFORE TRUNCATE ON public.agent_model_usage_event
      FOR EACH STATEMENT EXECUTE FUNCTION visit_prep_usage_truncate_guard();
    """)


def downgrade() -> None:
    raise RuntimeError('Retain the batch usage expand; recovery requires a forward fix.')
