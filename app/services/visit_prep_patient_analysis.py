"""Patient-requested protected clarification, not a provider chat or chart.

Core authorizes/protects the topic, commits intent, and issues a one-use grant.
Sovereign claims it under workload auth before inference and records usage back
here. No reply can be shown or accepted until current Core proposal persistence.
"""

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from typeid import TypeID

from app.configuration import visit_prep_clarification as configuration
from app.services.visit_prep_clarification_contract import (
    PatientClarificationReply,
    VisitPrepClarificationChanged,
)
from app.services.visit_prep_clarification_decisions import (
    _authorized,
    _decision_session,
    _operation,
    _proposal,
)
from app.services.visit_prep_clarification_proposals import (
    _proposal_context,
    record_followup_clarification_proposal,
    record_initial_clarification_proposal,
    select_patient_clarification_context,
)
from app.services.visit_prep_clarification_protection import (
    ground_protected_reply,
    protect_clarification_context,
)
from app.services.visit_prep_contribution_contract import ContributionBasis
from app.services.visit_prep_interview_contract import VisitPrepInterviewUnavailable
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_patient_continuation import (
    ConfirmedVisitPrepAccess,
    open_confirmation,
    seal_confirmation,
)
from app.services.visit_prep_protected_analysis import VisitPrepProtectionFailed
from app.services.visit_preps import _commit
from app.utils.encryption import decrypt_value, encrypt_value

from app.models.authority_usage import AgentModelUsageEvent
from app.models.visit_prep_clarification import VisitPrepClarificationProposal
from app.models.visit_prep_patient_analysis import VisitPrepPatientAnalysis
from sqlmodel import select


class PatientAnalysisUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Visit Prep clarification is temporarily unavailable")


class Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )


class PatientUsage(Closed):
    client_event_id: UUID
    call_kind: str
    call_sequence: int
    workstream: str
    provider: str
    provider_request_id: str | None = Field(default=None, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    deployment: str | None = Field(default=None, max_length=200)
    purpose: str
    started_at: AwareDatetime
    completed_at: AwareDatetime
    latency_ms: int = Field(ge=0, le=2**63 - 1)
    status: str
    usage_availability: str
    usage_source: str
    token_usage: dict[str, int | None]


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _grant(value):
    try:
        data = json.loads(decrypt_value(value))
        if (
            set(data)
            != {
                "purpose",
                "operation",
                "confirmation",
                "question",
                "basis",
                "source_turn",
                "context_digest",
                "topic_digest",
                "intent",
            }
            or data["purpose"] != "visit-prep.patient-analysis.v1"
        ):
            raise ValueError
        _operation(data["operation"])
        return data, open_confirmation(data["confirmation"])
    except Exception:
        raise PatientAnalysisUnavailable() from None


def claim_patient_analysis(*, grant: str, topic_digest: str, workload):
    """Claim exactly once, with fresh patient authority and exact protected body."""
    data, access = _grant(grant)
    with _decision_session() as session:
        authorized = _authorized(
            session, access_token=access, first_name="", role=access.actor_role
        )
        row = session.exec(
            select(VisitPrepPatientAnalysis)
            .where(VisitPrepPatientAnalysis.operation_id == UUID(data["operation"]))
            .with_for_update()
        ).one()
        intent = session.get(AgentModelUsageEvent, TypeID.from_string(data["intent"]))
        now = _database_now(session)
        current = _proposal_context(
            session, authorized, data["question"], data["source_turn"]
        )
        if (
            not configuration.SETTINGS.enabled
            or not workload.window.valid_at(now)
            or row.claimed
            or row.expires_at <= now
            or row.visit_prep_id != authorized.visit_prep.id
            or _digest(asdict(current)) != data["context_digest"]
            or topic_digest != data["topic_digest"]
            or intent is None
            or intent.patient_analysis_ref != row.operation_id
            or intent.workload_client_id != workload.client_id
        ):
            raise PatientAnalysisUnavailable()
        row.claimed = True
        session.add(row)
        _commit(session)
        return {"analysis_id": data["operation"], "model_profile": intent.model}


def record_patient_analysis_usage(*, grant: str, usage: PatientUsage, workload):
    """Usage survives stale answers; its immutable intent never grants disclosure."""
    data, _ = _grant(grant)
    counters = usage.token_usage
    if (
        set(counters)
        != {"input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"}
        or any(
            v is not None and (type(v) is not int or not 0 <= v <= 2**63 - 1)
            for v in counters.values()
        )
        or usage.call_kind != "drafting"
        or usage.call_sequence != 1
        or usage.workstream != "visit_prep"
        or usage.purpose != "visit_prep_patient_clarification"
        or usage.provider != "azure_openai"
        or usage.status not in {"succeeded", "failed", "cancelled"}
        or usage.completed_at < usage.started_at
        or usage.usage_availability
        != (
            "complete"
            if all(v is not None for v in counters.values())
            else "partial"
            if any(v is not None for v in counters.values())
            else "unavailable"
        )
        or usage.usage_source
        != (
            "not_available"
            if usage.usage_availability == "unavailable"
            else "provider_response"
        )
    ):
        raise PatientAnalysisUnavailable()
    digest = _digest(usage.model_dump(mode="json"))
    with _decision_session() as session:
        intent = session.exec(
            select(AgentModelUsageEvent)
            .where(AgentModelUsageEvent.id == TypeID.from_string(data["intent"]))
            .with_for_update()
        ).one()
        row = session.exec(
            select(VisitPrepPatientAnalysis).where(
                VisitPrepPatientAnalysis.operation_id == UUID(data["operation"])
            )
        ).one()
        now = _database_now(session)
        if (
            not workload.window.valid_at(now)
            or intent.event_origin != "visit_prep_patient"
            or intent.batch_phase != "intent"
            or not row.claimed
            or intent.workload_client_id != workload.client_id
            or intent.model != usage.model
            or intent.patient_analysis_ref != row.operation_id
            or intent.started_at is None
            or usage.started_at < intent.started_at
        ):
            raise PatientAnalysisUnavailable()
        existing = session.exec(
            select(AgentModelUsageEvent).where(
                AgentModelUsageEvent.call_intent_id == intent.id
            )
        ).one_or_none()
        if existing:
            if existing.request_digest != digest:
                raise PatientAnalysisUnavailable()
            return {
                "record_ref": str(existing.id),
                "recorded_at": existing.recorded_at.isoformat(),
            }
        values = {
            key: getattr(intent, key)
            for key in (
                "event_origin",
                "patient_analysis_ref",
                "workload_client_id",
                "core_practice_id",
                "core_doctor_id",
                "core_member_id",
                "preparation_ref",
                "source_version",
                "surface",
                "auth_tier",
            )
        }
        values.update(usage.model_dump(exclude={"token_usage"}))
        values.update(counters)
        event = AgentModelUsageEvent(
            **values,
            batch_phase="result",
            call_intent_id=intent.id,
            request_digest=digest,
            recorded_at=now,
        )
        session.add(event)
        session.flush()
        _commit(session)
        return {"record_ref": str(event.id), "recorded_at": now.isoformat()}


def request_patient_clarification(
    *,
    access_token,
    first_name,
    role,
    question_key,
    expected_basis: ContributionBasis,
    operation_id: str,
    source_turn_id=None,
):
    """A browser supplies only a topic/current basis, never a model reply or grant."""
    if not configuration.SETTINGS.enabled:
        raise PatientAnalysisUnavailable()
    operation = UUID(_operation(operation_id))
    arguments: dict[str, Any] = dict(
        access_token=access_token,
        first_name=first_name,
        role=role,
        question_key=question_key,
        expected_basis=expected_basis,
        source_turn_id=source_turn_id,
    )
    context = select_patient_clarification_context(**arguments)
    with _decision_session() as session:
        authorized = _authorized(
            session, access_token=access_token, first_name=first_name, role=role
        )
        existing = session.exec(
            select(VisitPrepPatientAnalysis).where(
                VisitPrepPatientAnalysis.operation_id == operation
            )
        ).one_or_none()
        if existing is not None:
            saved = session.exec(
                select(VisitPrepClarificationProposal).where(
                    VisitPrepClarificationProposal.visit_prep_id
                    == authorized.visit_prep.id,
                    VisitPrepClarificationProposal.operation_id == operation_id,
                )
            ).one_or_none()
            if saved is None or existing.visit_prep_id != authorized.visit_prep.id:
                raise PatientAnalysisUnavailable()
            _, evidence = _proposal(
                session, authorized=authorized, proposal_id=str(saved.id)
            )
            if evidence.context != context or evidence.source_turn_id != source_turn_id:
                raise VisitPrepClarificationChanged()
            _commit(session)
            from app.services.visit_prep_clarification_proposals import (
                InitialClarificationProposalReceipt,
            )

            return InitialClarificationProposalReceipt(
                str(saved.id), "replayed", context.basis
            )
    try:
        protected = protect_clarification_context(context)
    except VisitPrepProtectionFailed:
        raise PatientAnalysisUnavailable() from None
    # Token acquisition and de-id happen before the final current-source check.
    try:
        token = configuration.SETTINGS.token()
    except Exception:
        raise PatientAnalysisUnavailable() from None
    from app.configuration.service_auth import SOVEREIGN_TOOL_AUTH_SETTINGS

    if (
        not SOVEREIGN_TOOL_AUTH_SETTINGS.enabled
        or not SOVEREIGN_TOOL_AUTH_SETTINGS.is_complete()
    ):
        raise PatientAnalysisUnavailable()
    with _decision_session() as session:
        authorized = _authorized(
            session, access_token=access_token, first_name=first_name, role=role
        )
        if (
            _proposal_context(session, authorized, question_key, source_turn_id)
            != context
        ):
            raise VisitPrepClarificationChanged()
        existing = session.exec(
            select(VisitPrepPatientAnalysis).where(
                VisitPrepPatientAnalysis.operation_id == operation
            )
        ).one_or_none()
        if existing:
            # An uncertain attempt is never silently rerun or charged twice.
            raise PatientAnalysisUnavailable()
        now = _database_now(session)
        row = VisitPrepPatientAnalysis(
            visit_prep_id=authorized.visit_prep.id,
            operation_id=operation,
            requested_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        session.add(row)
        session.flush()
        intent = AgentModelUsageEvent(
            event_origin="visit_prep_patient",
            patient_analysis_ref=operation,
            workload_client_id=next(
                iter(SOVEREIGN_TOOL_AUTH_SETTINGS.allowed_client_ids)
            ),
            client_event_id=uuid4(),
            core_practice_id=authorized.visit_prep.practice_id,
            core_doctor_id=authorized.visit_prep.owner_doctor_id,
            core_member_id=authorized.visit_prep.owner_practice_member_id,
            preparation_ref=authorized.visit_prep.id,
            source_version=context.source_version,
            batch_phase="intent",
            surface="visit_prep_patient",
            auth_tier="recipient",
            call_kind="drafting",
            call_sequence=1,
            workstream="visit_prep",
            provider="azure_openai",
            model=configuration.SETTINGS.model_profile,
            purpose="visit_prep_patient_clarification",
            started_at=now,
            status="started",
            usage_availability="unavailable",
            usage_source="not_available",
            request_digest=_digest(
                {"operation": operation_id, "source_version": context.source_version}
            ),
            recorded_at=now,
        )
        session.add(intent)
        session.flush()
        intent_id = intent.id
        if authorized.visit_prep.confirmed_at is None:
            raise PatientAnalysisUnavailable()
        confirmation = ConfirmedVisitPrepAccess(
            authorized.credential.token_hash,
            role,
            authorized.visit_prep.confirmed_at,
            authorized.credential.expires_at,
        )
        grant = encrypt_value(
            json.dumps(
                dict(
                    purpose="visit-prep.patient-analysis.v1",
                    operation=operation_id,
                    confirmation=seal_confirmation(confirmation),
                    question=question_key,
                    basis=asdict(expected_basis),
                    source_turn=source_turn_id,
                    context_digest=_digest(asdict(context)),
                    topic_digest=_digest(protected.model_payload()),
                    intent=str(intent.id),
                )
            )
        )
        _commit(session)
    try:
        with httpx.Client(
            timeout=30, trust_env=False, follow_redirects=False
        ) as client:
            with client.stream(
                "POST",
                configuration.SETTINGS.endpoint,
                json=protected.model_payload(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Visit-Prep-Analysis-Grant": grant,
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity")
                    != "identity"
                ):
                    raise PatientAnalysisUnavailable()
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > 8192:
                        raise PatientAnalysisUnavailable()
        receipt = json.loads(body)
        if (
            set(receipt) != {"analysis_id", "usage_ref", "reply"}
            or receipt["analysis_id"] != operation_id
        ):
            raise PatientAnalysisUnavailable()
        with _decision_session() as session:
            result = session.get(
                AgentModelUsageEvent, TypeID.from_string(receipt["usage_ref"])
            )
            if (
                result is None
                or result.call_intent_id != intent_id
                or result.status != "succeeded"
            ):
                raise PatientAnalysisUnavailable()
            result_model = result.model
        if select_patient_clarification_context(**arguments) != context:
            raise VisitPrepClarificationChanged()
        if (
            not configuration.SETTINGS.enabled
            or configuration.SETTINGS.model_profile != result_model
        ):
            raise PatientAnalysisUnavailable()
        reply = ground_protected_reply(
            protected,
            PatientClarificationReply.model_validate_json(json.dumps(receipt["reply"])),
        )
        command = (
            record_followup_clarification_proposal
            if source_turn_id
            else record_initial_clarification_proposal
        )
        return command(
            access_token=access_token,
            first_name=first_name,
            role=role,
            operation_id=operation_id,
            context=context,
            reply=reply,
            model_profile=configuration.SETTINGS.model_profile,
            **({"source_turn_id": source_turn_id} if source_turn_id else {}),
        )
    except (VisitPrepClarificationChanged, VisitPrepInterviewUnavailable):
        raise
    except Exception:
        raise PatientAnalysisUnavailable() from None
