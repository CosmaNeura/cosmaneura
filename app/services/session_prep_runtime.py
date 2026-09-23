"""Durable brief reads and a recoverable PostgreSQL-backed preparation queue."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import ValidationError
from typeid import TypeID

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.services.provider_record_authority import (
    ProviderRecordScopeDenied,
    validate_provider_record_scope,
)
from app.services.session_prep import (
    SessionPrepAuthorization,
    SessionPrepIdempotencyConflict,
    SessionPrepInvariantError,
    SessionPrepInvocation,
    SessionPrepProvenance,
    SessionPrepResult,
    SessionPrepScopeDenied,
    SessionPrepToolResponse,
    _empty_result,
)
from app.services.session_prep_generator import (
    GENERATOR_VERSION,
    MODEL_PROFILE,
    PROMPT_VERSION,
)
from app.services.session_prep_note_sources import (
    ReviewedNoteSelection,
    load_reviewed_note_sources,
)

from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.session_prep_runtime import (
    SessionPrepArtifact,
    SessionPrepBuild,
    SessionPrepReadReceipt,
)
from sqlmodel import Session, select


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_manifest(selection: ReviewedNoteSelection) -> list[dict[str, Any]]:
    return [
        {
            "source_ref": note.source_ref,
            "source_version": note.source_version,
            "source_at": note.source_at.isoformat(),
            "content_sha256": note.content_sha256,
            "clinician_action_ref": note.clinician_action_ref,
        }
        for note in selection.sources
    ]


def source_digest(selection: ReviewedNoteSelection) -> str:
    return _digest(
        [
            source_manifest(selection),
            selection.truncated,
            GENERATOR_VERSION,
            PROMPT_VERSION,
            MODEL_PROFILE,
        ]
    )


def current_source_state(
    session: Session,
    scope: ProviderRecordAuthority,
    *,
    observed_at: datetime,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> tuple[ReviewedNoteSelection, str]:
    """Authorize worker and read access using current canonical platform state."""
    binding = session.get(
        AuthorityPersonBinding, scope.binding_id, populate_existing=True
    )
    doctor = session.get(Doctor, scope.doctor_id, populate_existing=True)
    if (
        binding is None
        or doctor is None
        or binding.actor_id is None
        or binding.tenant_id is None
    ):
        raise SessionPrepScopeDenied
    actor = session.get(AuthorityActor, binding.actor_id, populate_existing=True)
    tenant = session.get(AuthorityTenant, binding.tenant_id, populate_existing=True)
    identity = session.exec(
        select(AuthorityActorExternalIdentity).where(
            AuthorityActorExternalIdentity.actor_id == binding.actor_id,
            AuthorityActorExternalIdentity.subject == doctor.clerk_id,
            AuthorityActorExternalIdentity.issuer == settings.clerk_issuer,
        )
    ).one_or_none()
    membership = session.exec(
        select(AuthorityTenantMembership).where(
            AuthorityTenantMembership.actor_id == binding.actor_id,
            AuthorityTenantMembership.tenant_id == binding.tenant_id,
        )
    ).one_or_none()
    if (
        not settings.enabled
        or not settings.is_complete()
        or actor is None
        or actor.status != "active"
        or actor.actor_kind != "human"
        or tenant is None
        or tenant.status != "active"
        or identity is None
        or identity.revoked_at is not None
        or membership is None
        or membership.status != "active"
        or membership.membership_kind != "provider"
    ):
        raise SessionPrepScopeDenied
    try:
        validated = validate_provider_record_scope(
            session=session,
            binding=binding,
            actor_id=actor.id,
            clerk_subject=doctor.clerk_id,
        )
    except ProviderRecordScopeDenied:
        raise SessionPrepScopeDenied from None
    if validated is None or validated.id != scope.id:
        raise SessionPrepScopeDenied
    selection = load_reviewed_note_sources(
        session=session,
        doctor_id=doctor.id,
        target_appointment_id=scope.appointment_id,
        observed_at=observed_at,
    )
    return selection, source_digest(selection)


def schedule_scope_build(
    session: Session,
    scope: ProviderRecordAuthority,
    *,
    observed_at: datetime,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
    selection: ReviewedNoteSelection | None = None,
) -> SessionPrepBuild | None:
    # All schedulers serialize on this immutable scope row; the worker uses
    # the same scope-before-build lock order when it publishes a result.
    session.exec(
        select(ProviderRecordAuthority)
        .where(ProviderRecordAuthority.id == scope.id)
        .with_for_update()
    ).one()
    if selection is None:
        selection, digest = current_source_state(
            session, scope, observed_at=observed_at, settings=settings
        )
    else:
        digest = source_digest(selection)
    if not selection.sources:
        return None
    build = session.exec(
        select(SessionPrepBuild)
        .where(SessionPrepBuild.record_scope_id == scope.id)
        .with_for_update()
    ).one_or_none()
    if build is None:
        build = SessionPrepBuild(
            record_scope_id=scope.id,
            desired_digest=digest,
            requested_at=observed_at,
            retry_at=observed_at,
        )
    elif build.desired_digest != digest or (
        build.state == "failed" and observed_at >= build.retry_at
    ):
        build.desired_digest = digest
        build.state = "pending"
        build.attempts = 0
        build.failure_code = None
        build.lease_until = None
        build.requested_at = observed_at
        build.retry_at = observed_at
    session.add(build)
    session.flush()
    return build


def ready_artifact(
    session: Session, scope_id: TypeIDType, digest: str
) -> SessionPrepArtifact | None:
    return session.exec(
        select(SessionPrepArtifact).where(
            SessionPrepArtifact.record_scope_id == scope_id,
            SessionPrepArtifact.source_digest == digest,
        )
    ).one_or_none()


def artifact_result(artifact: SessionPrepArtifact) -> SessionPrepResult:
    try:
        result = SessionPrepResult.model_validate(artifact.result_payload)
    except ValidationError:
        raise SessionPrepInvariantError("Stored preparation is invalid") from None
    if (
        result.source_version_digest != f"sha256_{artifact.source_digest}"
        or result.state != "ready"
    ):
        raise SessionPrepScopeDenied
    return result


def invoke_provider_session_prep(
    *,
    session: Session,
    authorization: SessionPrepAuthorization,
    workload_client_id: str,
    idempotency_key: str,
    source_turn_ref: str,
    context_snapshot_ref: str,
    detail: str,
    observed_at: datetime,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> SessionPrepInvocation:
    """Caller must hold the current interaction lock and commit before egress."""
    authorized = authorization.authorized
    scope = session.exec(
        select(ProviderRecordAuthority).where(
            ProviderRecordAuthority.binding_id
            == authorized.mount_session.person_binding_id
        )
    ).one_or_none()
    if (
        scope is None
        or detail != "brief"
        or not hmac.compare_digest(scope.context_snapshot_ref, context_snapshot_ref)
    ):
        raise SessionPrepScopeDenied
    selection, digest = current_source_state(
        session, scope, observed_at=observed_at, settings=settings
    )
    request_digest = _digest([source_turn_ref, context_snapshot_ref, detail])
    receipt = session.exec(
        select(SessionPrepReadReceipt).where(
            SessionPrepReadReceipt.workload_client_id == workload_client_id,
            SessionPrepReadReceipt.interaction_id == authorized.lineage.id,
            SessionPrepReadReceipt.idempotency_key == idempotency_key,
        )
    ).one_or_none()
    artifact = ready_artifact(session, scope.id, digest)
    if receipt is not None:
        if receipt.request_digest != request_digest:
            raise SessionPrepIdempotencyConflict
        if receipt.record_scope_id != scope.id or receipt.retain_until <= observed_at:
            raise SessionPrepScopeDenied
        if receipt.artifact_id is not None and (
            artifact is None or receipt.artifact_id != artifact.id
        ):
            # A retry cannot resurrect content from a changed/revoked source.
            # Keep the immutable receipt, deny replay, and require a new action.
            raise SessionPrepScopeDenied
        try:
            replay = SessionPrepToolResponse.model_validate(receipt.response_payload)
        except ValidationError:
            raise SessionPrepInvariantError(
                "Stored preparation receipt is invalid"
            ) from None
        return SessionPrepInvocation(response=replay, replayed=True)
    if authorization.turn_closed:
        raise SessionPrepScopeDenied
    if artifact is not None:
        result = artifact_result(artifact)
    elif not selection.sources:
        result = _empty_result(
            detail="brief", state="unavailable", reason_code="no_eligible_sources"
        )
    else:
        build = schedule_scope_build(
            session,
            scope,
            observed_at=observed_at,
            settings=settings,
            selection=selection,
        )
        failed = build is not None and build.state == "failed"
        result = _empty_result(
            detail="brief",
            state="unavailable" if failed else "pending",
            reason_code="context_not_available" if failed else "generation_pending",
        )
    receipt = SessionPrepReadReceipt(
        workload_client_id=workload_client_id,
        interaction_id=authorized.lineage.id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        record_scope_id=scope.id,
        artifact_id=artifact.id if artifact is not None else None,
        response_payload={},
        recorded_at=observed_at,
        retain_until=observed_at + timedelta(hours=24),
    )
    # Allocate before serializing the immutable response; the mixin's SQL
    # default otherwise runs only at flush, after these references are needed.
    receipt.id = cast(TypeIDType, TypeID("prep_read"))
    response = SessionPrepToolResponse(
        tool_call_ref=str(receipt.id),
        tool_name="session-prep.context.read",
        tool_version="1.0.0",
        status="completed",
        approval_ref=None,
        access_intent_ref=None,
        access_outcome_ref=None,
        result_classification="phi_possible",
        result=result,
        recorded_at=observed_at,
    )
    receipt.response_payload = response.model_dump(mode="json")
    session.add(receipt)
    session.add(
        AuthorityAuditEvent(
            event_type="session_prep.context.read",
            outcome=result.state,
            occurred_at=observed_at,
            workload_client_id=workload_client_id,
            tenant_id=authorized.mount_session.tenant_id,
            actor_id=authorized.mount_session.actor_id,
            person_id=authorized.context.person_id,
            interaction_id=authorized.lineage.id,
            resource_type="session_prep_read_receipt",
            resource_ref=str(receipt.id),
            request_digest=request_digest,
        )
    )
    session.flush()
    return SessionPrepInvocation(response=response, replayed=False)


def prepare_artifact_result(
    *,
    artifact: SessionPrepArtifact,
    selection: ReviewedNoteSelection,
    prepared: object,
    snapshot_at: datetime,
    generated_at: datetime,
    predecessor: SessionPrepArtifact | None,
) -> SessionPrepResult:
    """Construct immutable lineage only after validating generated source citations."""
    from app.services.session_prep import SessionPrepPreparedBrief
    from app.services.session_prep_generator import validate_generated_brief

    brief = SessionPrepPreparedBrief.model_validate(prepared)
    validate_generated_brief(brief, selection)
    provenance = [
        SessionPrepProvenance(
            source_class="clinician_finalized_note",
            included=True,
            source_ref=note.source_ref,
            source_version=note.source_version,
            source_at=note.source_at,
            author_class="clinician",
            reason_code="eligible_finalized_note",
        )
        for note in selection.sources
    ]
    if selection.truncated:
        provenance.append(
            SessionPrepProvenance(
                source_class="clinician_finalized_note",
                included=False,
                source_ref=None,
                source_at=None,
                author_class=None,
                reason_code="selection_limit",
            )
        )
    provenance.append(
        SessionPrepProvenance(
            source_class="patient_reported_visit_prep",
            included=False,
            source_ref=None,
            source_at=None,
            author_class=None,
            reason_code="source_not_enabled",
        )
    )
    return SessionPrepResult(
        state="ready",
        detail="brief",
        freshness="current",
        completeness="partial" if selection.truncated else "complete",
        current_through=max(note.source_at for note in selection.sources),
        snapshot_ref=f"snapshot_{artifact.id}",
        snapshot_version=artifact.version,
        snapshot_generated_at=snapshot_at,
        snapshot_supersedes_ref=f"snapshot_{predecessor.id}" if predecessor else None,
        source_version_digest=f"sha256_{artifact.source_digest}",
        brief_ref=f"brief_{artifact.id}",
        brief_version=artifact.version,
        brief_generated_at=generated_at,
        brief_supersedes_ref=f"brief_{predecessor.id}" if predecessor else None,
        brief_generator_version=GENERATOR_VERSION,
        brief_prompt_version=PROMPT_VERSION,
        brief_model_profile=MODEL_PROFILE,
        source_count=len(selection.sources),
        provenance=provenance,
        prepared_brief=brief,
        supporting_context=None,
        retry_after_seconds=None,
        reason_code=None,
    )
