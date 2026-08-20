"""Append-only, content-free model-usage facts for governed agent turns."""

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
from sqlmodel import Field, UniqueConstraint


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
            "status IN ('succeeded', 'failed', 'cancelled')",
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
            "surface = 'provider_assistant'",
            name="ck_agent_model_usage_event_provider_surface",
        ),
        sa.CheckConstraint(
            "auth_tier = 'provider'",
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
    )

    interaction_id: TypeIDType = InteractionLineage.foreign_key(index=True)
    workload_client_id: str = Field(max_length=64)
    client_event_id: UUID = Field(index=True)
    tenant_id: TypeIDType = AuthorityTenant.foreign_key()
    actor_id: TypeIDType = AuthorityActor.foreign_key()
    person_id: TypeIDType = AuthorityPerson.foreign_key()
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
