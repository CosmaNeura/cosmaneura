"""Dormant, content-free batch accounting in Core's single model-usage ledger.

An intent commits before model work. A result is a separate immutable fact,
including unavailable usage after failures. A crash leaves an unmatched intent,
not a false zero-token success. Accounting never grants disclosure/publication.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

import orjson

from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_synthesis_builds import (
    VisitPrepBuildClaim,
    VisitPrepBuildDiscarded,
    _finish,
    _load_locked_claim,
)
from app.services.visit_prep_synthesis_contract import VisitPrepSynthesisSelection
from app.services.visit_prep_synthesis_generator import (
    MODEL_PROFILE,
    VisitPrepGenerationUsage,
)
from app.services.visit_preps import _commit, _visit_prep_session

from activemodel.types import TypeIDType
from app.models.authority_usage import AgentModelUsageEvent
from sqlmodel import select


class VisitPrepUsageUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Visit Prep usage recording is unavailable")


def begin_synthesis_usage(
    claim: VisitPrepBuildClaim, selection: VisitPrepSynthesisSelection
) -> TypeIDType:
    """One intent per fenced attempt; replay must NOT start another SDK call."""
    if not selection.sources:
        raise VisitPrepUsageUnavailable()
    with _visit_prep_session() as session:
        locked, build, current = _load_locked_claim(session, claim)
        if current != selection or current.source_version != claim.source_version:
            raise VisitPrepBuildDiscarded()
        existing = session.exec(
            select(AgentModelUsageEvent).where(
                AgentModelUsageEvent.build_ref == build.id,
                AgentModelUsageEvent.build_epoch == claim.epoch,
                AgentModelUsageEvent.build_attempt == claim.attempt,
                AgentModelUsageEvent.batch_phase == "intent",
            )
        ).one_or_none()
        if existing is not None:
            raise VisitPrepUsageUnavailable()
        now = _database_now(session)
        digest = hashlib.sha256(
            orjson.dumps(
                {
                    "build": str(build.id),
                    "epoch": claim.epoch,
                    "attempt": claim.attempt,
                    "source": claim.source_version,
                    "lease": claim.lease_until.isoformat(),
                    "model": MODEL_PROFILE,
                },
                option=orjson.OPT_SORT_KEYS,
            )
        ).hexdigest()
        event = AgentModelUsageEvent(
            event_origin="visit_prep_batch",
            workload_client_id="core_visit_prep",
            client_event_id=uuid4(),
            core_practice_id=locked.practice.id,
            core_doctor_id=locked.preparation.owner_doctor_id,
            core_member_id=locked.owner.id,
            preparation_ref=locked.preparation.id,
            build_ref=build.id,
            build_epoch=claim.epoch,
            build_attempt=claim.attempt,
            build_lease_until=claim.lease_until,
            source_version=claim.source_version,
            batch_phase="intent",
            surface="provider_dashboard",
            auth_tier="provider",
            call_kind="drafting",
            call_sequence=claim.attempt,
            workstream="visit_prep",
            provider="azure_openai",
            model=MODEL_PROFILE,
            purpose="previsit_synthesis",
            started_at=now,
            status="started",
            usage_availability="unavailable",
            usage_source="not_available",
            request_digest=digest,
            recorded_at=now,
        )
        session.add(event)
        session.flush()
        _finish(session, locked, "synthesis_sources_selected", build=build, claim=claim)
        return event.id


def _valid_usage(usage: VisitPrepGenerationUsage) -> None:
    if (
        usage.provider != "azure_openai"
        or usage.model_profile != MODEL_PROFILE
        or usage.status not in {"succeeded", "failed"}
        or usage.usage_availability not in {"complete", "unavailable"}
        or type(usage.latency_ms) is not int
        or not 0 <= usage.latency_ms <= 2**63 - 1
        or any(
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
            for value in (usage.started_at, usage.completed_at)
        )
        or usage.completed_at < usage.started_at
        or (
            usage.provider_request_id is not None
            and (
                not isinstance(usage.provider_request_id, str)
                or re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", usage.provider_request_id)
                is None
            )
        )
        or (
            usage.usage_availability == "complete"
            and not all(
                type(value) is int and 0 <= value <= 2**63 - 1
                for value in (usage.input_tokens, usage.output_tokens)
            )
        )
        or (
            usage.usage_availability == "unavailable"
            and (usage.input_tokens is not None or usage.output_tokens is not None)
        )
    ):
        raise VisitPrepUsageUnavailable()


def record_synthesis_usage(
    intent_id: TypeIDType, usage: VisitPrepGenerationUsage
) -> TypeIDType:
    """Append exact-replay result even if owner access/source/lease changed.

    A call already happened; suppressing accounting because its source became
    stale loses usage. This returns only an opaque fact, never patient content.
    The immutable intent is the accounting scope, NOT publication authority.
    """
    _valid_usage(usage)
    digest = hashlib.sha256(
        orjson.dumps(
            {"intent": str(intent_id), **asdict(usage)}, option=orjson.OPT_SORT_KEYS
        )
    ).hexdigest()
    with _visit_prep_session() as session:
        intent = session.exec(
            select(AgentModelUsageEvent)
            .where(AgentModelUsageEvent.id == intent_id)
            .with_for_update()
        ).one_or_none()
        if (
            intent is None
            or intent.event_origin != "visit_prep_batch"
            or intent.batch_phase != "intent"
            or intent.model != usage.model_profile
            or intent.started_at is None
            or usage.started_at < intent.started_at
        ):
            raise VisitPrepUsageUnavailable()
        existing = session.exec(
            select(AgentModelUsageEvent).where(
                AgentModelUsageEvent.call_intent_id == intent.id
            )
        ).one_or_none()
        if existing is not None:
            if existing.request_digest != digest:
                raise VisitPrepUsageUnavailable()
            return existing.id
        event = AgentModelUsageEvent(
            event_origin="visit_prep_batch",
            workload_client_id=intent.workload_client_id,
            client_event_id=uuid4(),
            core_practice_id=intent.core_practice_id,
            core_doctor_id=intent.core_doctor_id,
            core_member_id=intent.core_member_id,
            preparation_ref=intent.preparation_ref,
            build_ref=intent.build_ref,
            build_epoch=intent.build_epoch,
            build_attempt=intent.build_attempt,
            build_lease_until=intent.build_lease_until,
            source_version=intent.source_version,
            batch_phase="result",
            call_intent_id=intent.id,
            surface=intent.surface,
            auth_tier=intent.auth_tier,
            call_kind=intent.call_kind,
            call_sequence=intent.call_sequence,
            workstream=intent.workstream,
            provider=usage.provider,
            provider_request_id=usage.provider_request_id,
            model=intent.model,
            purpose=intent.purpose,
            started_at=usage.started_at,
            completed_at=usage.completed_at,
            latency_ms=usage.latency_ms,
            status=usage.status,
            usage_availability=usage.usage_availability,
            usage_source="provider_response"
            if usage.usage_availability == "complete"
            else "not_available",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            request_digest=digest,
            recorded_at=_database_now(session),
        )
        session.add(event)
        session.flush()
        _commit(session)
        return event.id
