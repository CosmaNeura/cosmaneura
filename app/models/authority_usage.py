"""One content-free ledger for governed agent turns and Core batch calls."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityPerson,
    AuthorityTenant,
    InteractionLineage,
)
from sqlmodel import Column, Field, UniqueConstraint

PATIENT_USAGE_SHAPE = """
(event_origin = 'visit_prep_patient' AND interaction_id IS NULL AND
tenant_id IS NULL AND actor_id IS NULL AND person_id IS NULL AND
patient_analysis_ref IS NOT NULL AND core_practice_id IS NOT NULL AND
core_doctor_id IS NOT NULL AND core_member_id IS NOT NULL AND preparation_ref IS NOT NULL AND
source_version IS NOT NULL AND source_version ~ '^[0-9a-f]{64}$' AND build_ref IS NULL AND build_epoch IS NULL AND
build_attempt IS NULL AND build_lease_until IS NULL AND call_sequence = 1 AND
workstream = 'visit_prep' AND purpose = 'visit_prep_patient_clarification' AND
call_kind = 'drafting' AND provider = 'azure_openai' AND model IS NOT NULL AND
started_at IS NOT NULL AND batch_phase IS NOT NULL AND
((batch_phase = 'intent' AND status = 'started' AND call_intent_id IS NULL AND
completed_at IS NULL AND latency_ms IS NULL AND provider_request_id IS NULL AND
usage_availability = 'unavailable' AND usage_source = 'not_available' AND
input_tokens IS NULL AND output_tokens IS NULL AND cached_input_tokens IS NULL AND reasoning_tokens IS NULL) OR
(batch_phase = 'result' AND status IN ('succeeded', 'failed', 'cancelled') AND
call_intent_id IS NOT NULL AND completed_at IS NOT NULL AND completed_at >= started_at AND
latency_ms IS NOT NULL AND
((usage_availability IN ('complete', 'partial') AND usage_source = 'provider_response') OR
(usage_availability = 'unavailable' AND usage_source = 'not_available' AND
input_tokens IS NULL AND output_tokens IS NULL AND cached_input_tokens IS NULL AND reasoning_tokens IS NULL)))))
"""


class AgentModelUsageEvent(
    BaseModel,
    TypeIDMixin("model_usage"),
    table=True,
):
    """Append-only provider-reported model call without content or PHI."""

    __table_args__ = (
        UniqueConstraint(
            "workload_client_id",
            "interaction_id",
            "client_event_id",
            name="uq_agent_model_usage_event_logical",
        ),
        UniqueConstraint(
            "interaction_id",
            "call_sequence",
            name="uq_agent_model_usage_event_call_sequence",
        ),
        sa.CheckConstraint(
            "call_kind IN "
            "('classification', 'routing', 'reasoning', 'drafting', "
            "'doctrinal_arbitration', 'tool_support', 'embedding', 'other')",
            name="ck_agent_model_usage_event_call_kind",
        ),
        sa.CheckConstraint(
            "(event_origin = 'agent_turn' AND status IN "
            "('succeeded', 'failed', 'cancelled')) OR "
            "(event_origin IN ('visit_prep_batch', 'visit_prep_patient') AND "
            "((batch_phase = 'intent' AND status = 'started') OR "
            "(batch_phase = 'result' AND status IN ('succeeded', 'failed', 'cancelled'))))",
            name="ck_agent_model_usage_event_status",
        ),
        sa.CheckConstraint(
            "usage_availability IN ('complete', 'partial', 'unavailable')",
            name="ck_agent_model_usage_event_availability",
        ),
        sa.CheckConstraint(
            "usage_source IN "
            "('provider_response', 'provider_headers', 'platform_meter', "
            "'not_available')",
            name="ck_agent_model_usage_event_source",
        ),
        sa.CheckConstraint(
            "(event_origin = 'agent_turn' AND surface = 'provider_assistant') OR "
            "(event_origin = 'visit_prep_batch' AND surface = 'provider_dashboard') OR "
            "(event_origin = 'visit_prep_patient' AND surface = 'visit_prep_patient')",
            name="ck_agent_model_usage_event_provider_surface",
        ),
        sa.CheckConstraint(
            "(event_origin IN ('agent_turn', 'visit_prep_batch') AND auth_tier = 'provider') OR "
            "(event_origin = 'visit_prep_patient' AND auth_tier = 'recipient')",
            name="ck_agent_model_usage_event_provider_auth_tier",
        ),
        sa.CheckConstraint(
            "call_sequence >= 1",
            name="ck_agent_model_usage_event_call_sequence_positive",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_agent_model_usage_event_latency_nonnegative",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_agent_model_usage_event_input_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_agent_model_usage_event_output_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
            name="ck_agent_model_usage_event_cached_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "reasoning_tokens IS NULL OR reasoning_tokens >= 0",
            name="ck_agent_model_usage_event_reasoning_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "(event_origin = 'agent_turn' AND patient_analysis_ref IS NULL AND interaction_id IS NOT NULL AND "
            "tenant_id IS NOT NULL AND actor_id IS NOT NULL AND person_id IS NOT NULL "
            "AND batch_phase IS NULL AND call_intent_id IS NULL AND "
            "core_practice_id IS NULL AND core_doctor_id IS NULL AND "
            "core_member_id IS NULL AND preparation_ref IS NULL AND "
            "build_ref IS NULL AND build_epoch IS NULL AND build_attempt IS NULL "
            "AND source_version IS NULL AND build_lease_until IS NULL) OR "
            "(event_origin = 'visit_prep_batch' AND patient_analysis_ref IS NULL AND interaction_id IS NULL AND "
            "tenant_id IS NULL AND actor_id IS NULL AND person_id IS NULL AND "
            "batch_phase IS NOT NULL AND batch_phase IN ('intent', 'result') "
            "AND core_practice_id IS NOT NULL "
            "AND core_doctor_id IS NOT NULL AND core_member_id IS NOT NULL AND "
            "preparation_ref IS NOT NULL AND build_ref IS NOT NULL AND "
            "build_epoch IS NOT NULL AND build_epoch > 0 AND "
            "build_attempt IS NOT NULL AND build_attempt BETWEEN 1 AND 3 AND "
            "source_version IS NOT NULL AND length(source_version) = 64 AND "
            "build_lease_until IS NOT NULL AND call_sequence = build_attempt AND "
            "workload_client_id = 'core_visit_prep' AND workstream = 'visit_prep' "
            "AND purpose = 'previsit_synthesis' AND call_kind = 'drafting' AND "
            "provider = 'azure_openai' AND model IS NOT NULL AND started_at IS NOT NULL "
            "AND ((batch_phase = 'intent' AND call_intent_id IS NULL AND "
            "completed_at IS NULL AND latency_ms IS NULL AND "
            "provider_request_id IS NULL AND usage_availability = 'unavailable' "
            "AND usage_source = 'not_available' AND input_tokens IS NULL AND "
            "output_tokens IS NULL AND cached_input_tokens IS NULL AND "
            "reasoning_tokens IS NULL) OR (batch_phase = 'result' AND "
            "call_intent_id IS NOT NULL AND completed_at IS NOT NULL AND "
            "completed_at >= started_at AND latency_ms IS NOT NULL AND "
            "cached_input_tokens IS NULL AND reasoning_tokens IS NULL AND "
            "((usage_availability = 'complete' AND usage_source = 'provider_response' "
            "AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL) OR "
            "(usage_availability = 'unavailable' AND usage_source = 'not_available' "
            "AND input_tokens IS NULL AND output_tokens IS NULL))))) OR "
            + PATIENT_USAGE_SHAPE,
            name="ck_model_usage_origin_shape",
        ),
        UniqueConstraint(
            "build_ref",
            "build_epoch",
            "build_attempt",
            "batch_phase",
            name="uq_model_usage_batch_phase",
        ),
        UniqueConstraint("call_intent_id", name="uq_model_usage_intent_result"),
        UniqueConstraint(
            "patient_analysis_ref", "batch_phase", name="uq_model_usage_patient_phase"
        ),
    )

    event_origin: str = Field(
        default="agent_turn",
        sa_column=Column(sa.String(32), nullable=False, server_default="agent_turn"),
    )
    patient_analysis_ref: UUID | None = Field(default=None, nullable=True)
    interaction_id: TypeIDType | None = InteractionLineage.foreign_key(
        default=None, nullable=True, index=True
    )
    workload_client_id: str = Field(max_length=64)
    client_event_id: UUID = Field(index=True)
    tenant_id: TypeIDType | None = AuthorityTenant.foreign_key(
        default=None, nullable=True
    )
    actor_id: TypeIDType | None = AuthorityActor.foreign_key(
        default=None, nullable=True
    )
    person_id: TypeIDType | None = AuthorityPerson.foreign_key(
        default=None, nullable=True
    )
    # Observational Core references are NOT authority-spine bindings or chart
    # creation. No parent FK: content-free usage survives clinical root cleanup.
    core_practice_id: TypeIDType | None = Field(
        default=None, sa_column=Column(TypeIDType("practice"), nullable=True)
    )
    core_doctor_id: TypeIDType | None = Field(
        default=None, sa_column=Column(TypeIDType("doctor"), nullable=True)
    )
    core_member_id: TypeIDType | None = Field(
        default=None, sa_column=Column(TypeIDType("practice_member"), nullable=True)
    )
    preparation_ref: TypeIDType | None = Field(
        default=None, sa_column=Column(TypeIDType("visit_prep"), nullable=True)
    )
    build_ref: TypeIDType | None = Field(
        default=None, sa_column=Column(TypeIDType("visit_prep_build"), nullable=True)
    )
    build_epoch: int | None = Field(default=None)
    build_attempt: int | None = Field(default=None)
    build_lease_until: datetime | None = Field(
        default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True)
    )
    source_version: str | None = Field(default=None, max_length=64)
    batch_phase: str | None = Field(default=None, max_length=16)
    call_intent_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("model_usage"),
            sa.ForeignKey("agent_model_usage_event.id"),
            nullable=True,
        ),
    )
    surface: str = Field(max_length=32)
    auth_tier: str = Field(max_length=16)
    call_kind: str = Field(max_length=32)
    call_sequence: int = Field(sa_type=sa.BigInteger)  # type: ignore
    workstream: str = Field(max_length=128, index=True)
    provider: str = Field(max_length=128, index=True)
    provider_request_id: str | None = Field(default=None, nullable=True, max_length=200)
    model: str | None = Field(default=None, nullable=True, max_length=200)
    deployment: str | None = Field(default=None, nullable=True, max_length=200)
    purpose: str = Field(max_length=128)
    started_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    completed_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    latency_ms: int | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.BigInteger,  # type: ignore
    )
    status: str = Field(max_length=16)
    usage_availability: str = Field(max_length=16)
    usage_source: str = Field(max_length=32)
    input_tokens: int | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.BigInteger,  # type: ignore
    )
    output_tokens: int | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.BigInteger,  # type: ignore
    )
    cached_input_tokens: int | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.BigInteger,  # type: ignore
    )
    reasoning_tokens: int | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.BigInteger,  # type: ignore
    )
    request_digest: str = Field(min_length=64, max_length=64)
    recorded_at: datetime = Field(
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


@sa.event.listens_for(AgentModelUsageEvent, "before_update")
def _prevent_model_usage_update(*_: object) -> None:
    raise RuntimeError("Agent model-usage events are append-only.")


@sa.event.listens_for(AgentModelUsageEvent, "before_delete")
def _prevent_model_usage_delete(*_: object) -> None:
    raise RuntimeError("Agent model-usage events are append-only.")
