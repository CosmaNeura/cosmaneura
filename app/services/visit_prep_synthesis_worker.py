"""Recoverable source-only generation; no patient text in broker work items.

Core's durable queue owns attempts and leases. Model work runs without database
locks, after a committed central-ledger intent. A crash may leave an unmatched
intent: that is uncertain usage, never an invented completion or zero tokens.
Only current sources, authority, lease and a recorded successful result permit
atomic artifact/job/audit publication. No conversational AI is implemented here.
"""

from __future__ import annotations

from typing import Literal

from app.services.visit_prep_packages import _audit, _authorize, _database_now
from app.services.visit_prep_protected_analysis import (
    VisitPrepProtectedAnalysis,
    VisitPrepProtectionFailed,
    protect_patient_analysis,
)
from app.services.visit_prep_synthesis_builds import (
    VisitPrepBuildClaim,
    VisitPrepBuildDiscarded,
    _load_locked_claim,
    _matches,
    claim_visit_prep_synthesis,
    fail_visit_prep_synthesis_claim,
    load_claimed_synthesis_sources,
)
from app.services.visit_prep_synthesis_contract import (
    VisitPrepPreparedBrief,
    VisitPrepSynthesisInvalid,
    VisitPrepSynthesisSelection,
)
from app.services.visit_prep_synthesis_generator import (
    MODEL_PROFILE,
    VisitPrepGenerationFailed,
    VisitPrepGenerationUsage,
    generate_patient_brief,
)
from app.services.visit_prep_synthesis_publication import (
    VisitPrepPublicationReceipt,
    VisitPrepSynthesisStale,
    _publish_locked,
)
from app.services.visit_prep_synthesis_usage import (
    VisitPrepUsageUnavailable,
    begin_synthesis_usage,
    record_synthesis_usage,
)
from app.services.visit_preps import (
    VisitPrepConflict,
    VisitPrepNotFound,
    _commit,
    _visit_prep_session,
)

from activemodel.types import TypeIDType
from app.models.authority_usage import AgentModelUsageEvent
from sqlmodel import select

WorkerOutcome = Literal[
    "idle", "ready", "discarded", "superseded", "generation_failed", "usage_unavailable"
]


def publish_claimed_synthesis(
    claim: VisitPrepBuildClaim,
    selection: VisitPrepSynthesisSelection,
    prepared: VisitPrepPreparedBrief,
    *,
    intent_id: TypeIDType | None,
    protected: VisitPrepProtectedAnalysis | None = None,
) -> VisitPrepPublicationReceipt:
    """Commit artifact, ready job and audit together under the original fence."""
    if intent_id is not None and getattr(intent_id, "prefix", None) != "model_usage":
        raise VisitPrepUsageUnavailable()
    with _visit_prep_session() as session:
        locked, build, current = _load_locked_claim(session, claim)
        if current != selection or current.source_version != claim.source_version:
            raise VisitPrepSynthesisStale()
        if current.sources:
            intent = session.get(AgentModelUsageEvent, intent_id) if intent_id else None
            result = (
                session.exec(
                    select(AgentModelUsageEvent).where(
                        AgentModelUsageEvent.call_intent_id == intent_id
                    )
                ).one_or_none()
                if intent_id
                else None
            )
            if (
                intent is None
                or result is None
                or intent.event_origin != "visit_prep_batch"
                or intent.batch_phase != "intent"
                or intent.preparation_ref != locked.preparation.id
                or intent.core_practice_id != locked.practice.id
                or intent.core_doctor_id != locked.preparation.owner_doctor_id
                or intent.core_member_id != locked.owner.id
                or intent.build_ref != claim.build_id
                or intent.build_epoch != claim.epoch
                or intent.build_attempt != claim.attempt
                or intent.build_lease_until != claim.lease_until
                or intent.source_version != claim.source_version
                or intent.model != MODEL_PROFILE
                or result.event_origin != "visit_prep_batch"
                or result.batch_phase != "result"
                or result.status != "succeeded"
            ):
                raise VisitPrepUsageUnavailable()
            # The ledger's immutable self-FK/insert guard binds the result's
            # entire context to its intent. Explicit unavailable token counts
            # are allowed; a missing/uncommitted result is never allowed.
        elif intent_id is not None:
            raise VisitPrepUsageUnavailable()
        receipt = _publish_locked(
            session, locked, selection, prepared, protected=protected
        )
        # Check AFTER encryption/flush, before clearing the running lease.
        now = _database_now(session)
        if not _matches(build, claim, now):
            raise VisitPrepBuildDiscarded()
        _authorize(session, owner=locked.owner, practice=locked.practice, at=now)
        _audit(
            session,
            owner=locked.owner,
            prep_id=locked.preparation.id,
            source_version=current.source_version,
            at=now,
            operation="synthesis_published",
        )
        session.flush()
        # An audit write can itself wait. Do not clear the lease until the
        # final database-owned clock and time-dependent authority pass.
        now = _database_now(session)
        if not _matches(build, claim, now):
            raise VisitPrepBuildDiscarded()
        _authorize(session, owner=locked.owner, practice=locked.practice, at=now)
        build.state = "ready"
        build.lease_until = build.failure_code = None
        session.add(build)
        _commit(session)
        return receipt


def _fail(
    claim: VisitPrepBuildClaim,
    code: Literal["generation_failed", "usage_unavailable"],
) -> WorkerOutcome:
    return code if fail_visit_prep_synthesis_claim(claim, code=code) else "discarded"


def run_one_build() -> WorkerOutcome:
    """Drain one request; DB leases, not Celery autoretries, bound model calls.

    Storage failures may propagate to the metadata-only job wrapper. No result,
    exception payload or patient text is logged or returned by this service.
    """
    claim = claim_visit_prep_synthesis()
    if claim is None:
        return "idle"
    intent_id = None
    recording_failed = False
    result_recorded = False

    def record(usage: VisitPrepGenerationUsage) -> None:
        nonlocal recording_failed, result_recorded
        try:
            if intent_id is None:
                raise VisitPrepUsageUnavailable()
            record_synthesis_usage(intent_id, usage)
            result_recorded = True
        except Exception:
            recording_failed = True
            raise VisitPrepUsageUnavailable() from None

    try:
        selection = load_claimed_synthesis_sources(claim)
        protected = protect_patient_analysis(selection)
        # The provider operation holds no database locks. Reacquire current
        # source/identity/time/lease fences before a model intent and request.
        current = load_claimed_synthesis_sources(claim)
        if current != selection:
            raise VisitPrepSynthesisStale()
        if selection.sources:
            intent_id = begin_synthesis_usage(claim, selection)
        prepared = generate_patient_brief(
            selection, record_usage=record, protected=protected
        )
        if selection.sources and (recording_failed or not result_recorded):
            raise VisitPrepUsageUnavailable()
        publish_claimed_synthesis(
            claim, selection, prepared, intent_id=intent_id, protected=protected
        )
        return "ready"
    except VisitPrepSynthesisStale:
        # Reacquire current authority and requeue the corrected source epoch.
        # This cannot release someone else's lease or reuse old generated text.
        try:
            load_claimed_synthesis_sources(claim)
        except VisitPrepBuildDiscarded:
            return "superseded"
        return _fail(claim, "generation_failed")
    except VisitPrepBuildDiscarded:
        return "discarded"
    except VisitPrepUsageUnavailable:
        return _fail(claim, "usage_unavailable")
    except (
        VisitPrepGenerationFailed,
        VisitPrepSynthesisInvalid,
        VisitPrepProtectionFailed,
    ):
        if recording_failed or (intent_id is not None and not result_recorded):
            return _fail(claim, "usage_unavailable")
        return _fail(claim, "generation_failed")
    except (VisitPrepNotFound, VisitPrepConflict):
        fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")
        return "discarded"
