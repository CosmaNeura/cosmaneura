"""Turn-bound preparation and opt-in reviewed-source evidence for provider questions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import app.services.authority as authority
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.services.session_prep import (
    OpaqueRef,
    SessionPrepIdempotencyConflict,
    SessionPrepInvariantError,
    SessionPrepPreparedBrief,
    SessionPrepProvenance,
    SessionPrepScopeDenied,
    _aware,
)
from app.services.session_prep_note_sources import (
    MAX_FINALIZED_NOTES,
    MAX_SOURCE_CHARACTERS,
    ReviewedNoteSelection,
    canonical_note_content,
    load_reviewed_note_sources,
)
from app.services.session_prep_runtime import (
    artifact_result,
    current_source_state,
    ready_artifact,
    source_digest,
    source_manifest,
)
from app.services.session_prep_source_locks import (
    NoteSourceScopeChanged,
    lock_note_source_versions,
)

from app.models.authority import AuthorityAuditEvent, AuthorityIdempotencyRecord
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.session_prep_runtime import SessionPrepArtifact, SessionPrepBuild
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

OPERATION = "provider-question.context.read"
TOOL_KEY = (OPERATION, "1.0.0")
REVIEWED_SOURCES_TOOL_KEY = (OPERATION, "1.1.0")
REVIEWED_SOURCES_ONLY_TOOL_KEY = (OPERATION, "1.2.0")
TOOL_KEYS = frozenset(
    {TOOL_KEY, REVIEWED_SOURCES_TOOL_KEY, REVIEWED_SOURCES_ONLY_TOOL_KEY}
)
RETENTION = timedelta(hours=24)
REVIEWED_SOURCES_MARKER = "reviewed_sources|"
REVIEWED_SOURCES_ONLY_MARKER = "reviewed_sources_only|"


class ProviderContextChanged(SessionPrepScopeDenied):
    """A context read cannot authorize acceptance of the current final answer."""


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderQuestionArguments(_Closed):
    source_turn_ref: OpaqueRef
    purpose: Literal["provider_question"]
    detail: Literal["prep_summary", "reviewed_sources"]


class ProviderQuestionInvocation(_Closed):
    tool_name: Literal["provider-question.context.read"]
    tool_version: Literal["1.0.0", "1.1.0", "1.2.0"]
    subject_ref: None
    arguments: ProviderQuestionArguments

    @model_validator(mode="after")
    def detail_matches_version(self) -> ProviderQuestionInvocation:
        expected = (
            "prep_summary" if self.tool_version == "1.0.0" else "reviewed_sources"
        )
        if self.arguments.detail != expected:
            raise ValueError("Question context detail does not match its version")
        return self


class ProviderQuestionResult(_Closed):
    schema_version: Literal["1.0.0"] = "1.0.0"
    state: Literal["ready", "pending", "unavailable"]
    reason_code: (
        Literal["generation_pending", "no_eligible_sources", "context_not_available"]
        | None
    )
    source_version_digest: str = Field(pattern=r"^sha256_[a-f0-9]{64}$")
    prepared_brief: SessionPrepPreparedBrief | None
    provenance: list[SessionPrepProvenance] = Field(max_length=5)
    completeness: Literal["complete", "partial", "none"]
    current_through: datetime | None
    generated_at: datetime | None

    _timestamps_are_aware = field_validator("current_through", "generated_at")(_aware)

    @model_validator(mode="after")
    def consistent(self) -> ProviderQuestionResult:
        if self.state == "ready":
            if (
                self.reason_code is not None
                or self.prepared_brief is None
                or self.prepared_brief.patient_reported is not None
                or not self.provenance
                or self.completeness == "none"
                or self.current_through is None
                or self.generated_at is None
            ):
                raise ValueError("Invalid preparation summary")
            if any(
                not p.included or p.source_class != "clinician_finalized_note"
                for p in self.provenance
            ):
                raise ValueError("Only reviewed clinician sources are available")
        elif (
            self.prepared_brief is not None
            or self.provenance
            or self.completeness != "none"
            or self.current_through is not None
            or self.generated_at is not None
            or self.reason_code is None
            or (self.state == "pending") != (self.reason_code == "generation_pending")
        ):
            raise ValueError("Unavailable preparation cannot carry context")
        return self


class ProviderReviewedSource(_Closed):
    source_ref: OpaqueRef
    source_version: int = Field(ge=1, le=2_147_483_647)
    source_at: datetime
    sections: dict[str, str] = Field(min_length=1, max_length=64, repr=False)

    _source_date_is_aware = field_validator("source_at")(_aware)

    @field_validator("sections")
    @classmethod
    def bounded_reviewed_sections(cls, value: dict[str, str]) -> dict[str, str]:
        # Match the review adapter's existing per-note bound and canonical
        # representation; no transcript or arbitrary record serialization.
        canonical_note_content(value)
        return value


class ProviderQuestionSourcesResult(ProviderQuestionResult):
    schema_version: Literal["1.1.0"] = "1.1.0"  # type: ignore[assignment]
    reviewed_sources: list[ProviderReviewedSource] = Field(
        max_length=MAX_FINALIZED_NOTES, repr=False
    )

    @model_validator(mode="after")
    def sources_match_reviewed_provenance(self) -> ProviderQuestionSourcesResult:
        expected = {
            p.source_ref: (p.source_version, p.source_at) for p in self.provenance
        }
        actual = {
            s.source_ref: (s.source_version, s.source_at) for s in self.reviewed_sources
        }
        if len(actual) != len(self.reviewed_sources) or actual != expected:
            raise ValueError("Reviewed source evidence does not match provenance")
        if (
            sum(
                len(canonical_note_content(source.sections))
                for source in self.reviewed_sources
            )
            > MAX_FINALIZED_NOTES * MAX_SOURCE_CHARACTERS
        ):
            raise ValueError("Reviewed source evidence exceeds its aggregate bound")
        return self


class ProviderQuestionSourcesOnlyResult(_Closed):
    """Source availability is independent of optional model preparation."""

    schema_version: Literal["1.2.0"] = "1.2.0"
    state: Literal["ready", "unavailable"]
    reason_code: Literal["no_eligible_sources"] | None
    source_version_digest: str = Field(pattern=r"^sha256_[a-f0-9]{64}$")
    brief_state: Literal["ready", "pending", "unavailable"]
    prepared_brief: SessionPrepPreparedBrief | None
    provenance: list[SessionPrepProvenance] = Field(max_length=MAX_FINALIZED_NOTES)
    completeness: Literal["complete", "partial", "none"]
    current_through: datetime | None
    generated_at: datetime | None
    reviewed_sources: list[ProviderReviewedSource] = Field(
        max_length=MAX_FINALIZED_NOTES, repr=False
    )

    _timestamps_are_aware = field_validator("current_through", "generated_at")(_aware)

    @model_validator(mode="after")
    def consistent(self) -> ProviderQuestionSourcesOnlyResult:
        if self.state == "unavailable":
            if (
                self.reason_code != "no_eligible_sources"
                or self.brief_state != "unavailable"
                or self.prepared_brief is not None
                or self.generated_at is not None
                or self.current_through is not None
                or self.provenance
                or self.reviewed_sources
                or self.completeness != "none"
            ):
                raise ValueError("Unavailable sources cannot carry context")
            return self
        if (
            self.reason_code is not None
            or not self.provenance
            or not self.reviewed_sources
            or self.completeness == "none"
            or self.current_through is None
            or any(
                not p.included or p.source_class != "clinician_finalized_note"
                for p in self.provenance
            )
        ):
            raise ValueError("Invalid reviewed source context")
        if self.brief_state == "ready":
            if self.prepared_brief is None or self.generated_at is None:
                raise ValueError("Ready brief requires generated preparation")
            if self.prepared_brief.patient_reported is not None:
                raise ValueError("Only reviewed clinician sources are available")
        elif self.prepared_brief is not None or self.generated_at is not None:
            raise ValueError("Unavailable brief cannot carry generated preparation")
        expected = {
            p.source_ref: (p.source_version, p.source_at) for p in self.provenance
        }
        actual = {
            s.source_ref: (s.source_version, s.source_at) for s in self.reviewed_sources
        }
        if len(actual) != len(self.reviewed_sources) or actual != expected:
            raise ValueError("Reviewed source evidence does not match provenance")
        if (
            max(source.source_at for source in self.reviewed_sources)
            != self.current_through
        ):
            raise ValueError("Reviewed source cutoff does not match its evidence")
        if (
            sum(
                len(canonical_note_content(source.sections))
                for source in self.reviewed_sources
            )
            > MAX_FINALIZED_NOTES * MAX_SOURCE_CHARACTERS
        ):
            raise ValueError("Reviewed source evidence exceeds its aggregate bound")
        return self


ProviderQuestionResultLike = ProviderQuestionResult | ProviderQuestionSourcesOnlyResult


class ProviderQuestionResponse(_Closed):
    tool_call_ref: OpaqueRef
    status: Literal["completed"] = "completed"
    result: (
        ProviderQuestionResult
        | ProviderQuestionSourcesResult
        | ProviderQuestionSourcesOnlyResult
    )


class ProviderQuestionRead(NamedTuple):
    response: ProviderQuestionResponse
    replayed: bool


def _binding_digest(source_turn_ref: str, result: ProviderQuestionResultLike) -> str:
    # The immutable receipt binds the exact v1 request AND the complete returned
    # state/content/provenance. Non-ready receipts cannot be upgraded in place.
    return hashlib.sha256(
        json.dumps(
            {
                "request": {
                    "tool_name": OPERATION,
                    "tool_version": result.schema_version,
                    "subject_ref": None,
                    "arguments": {
                        "source_turn_ref": source_turn_ref,
                        "purpose": "provider_question",
                        "detail": (
                            "reviewed_sources"
                            if result.schema_version != "1.0.0"
                            else "prep_summary"
                        ),
                    },
                },
                "result": result.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _turn_and_scope(
    session: Session,
    authorized: authority.AuthorizedInteraction,
    source_turn_ref: str,
) -> ProviderRecordAuthority:
    context, mount = authorized.context, authorized.mount_session
    if (
        context.surface != "provider_assistant"
        or context.auth_tier != "provider"
        or "session-prep:read" not in context.capabilities
        or not {"education:answer", "ethics:consult", "visit-prep:run"}.intersection(
            context.capabilities
        )
        or context.session_prep_context_ref is None
    ):
        raise SessionPrepScopeDenied
    turn = session.exec(
        select(CanonicalConversationTurn).where(
            CanonicalConversationTurn.interaction_id == authorized.lineage.id
        )
    ).one_or_none()
    if (
        turn is None
        or str(turn.id) != source_turn_ref
        or turn.workload_client_id != authorized.lineage.workload_client_id
        or turn.client_turn_id != authorized.lineage.client_turn_id
        or turn.actor_id != mount.actor_id
        or turn.tenant_id != mount.tenant_id
        or turn.person_id != context.person_id
    ):
        raise SessionPrepScopeDenied
    user = session.exec(
        select(CanonicalConversationUserPhase).where(
            CanonicalConversationUserPhase.turn_id == turn.id
        )
    ).one_or_none()
    if user is None or user.ingress_kind != "chat_text" or user.action_code is not None:
        raise SessionPrepScopeDenied
    scope = session.exec(
        select(ProviderRecordAuthority).where(
            ProviderRecordAuthority.binding_id == mount.person_binding_id
        )
    ).one_or_none()
    if scope is None or scope.context_snapshot_ref != context.session_prep_context_ref:
        raise SessionPrepScopeDenied
    return scope


def _read_receipt(
    session: Session, authorized: authority.AuthorizedInteraction
) -> AuthorityIdempotencyRecord | None:
    return session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id
            == authorized.lineage.workload_client_id,
            AuthorityIdempotencyRecord.interaction_id == authorized.lineage.id,
            AuthorityIdempotencyRecord.operation == OPERATION,
        )
    ).one_or_none()


def _current_result(
    session: Session,
    scope: ProviderRecordAuthority,
    observed_at: datetime,
    settings: AuthorityKernelSettings,
    marker: str | None = None,
    include_sources: bool | None = None,
    source_only: bool | None = None,
) -> tuple[ProviderQuestionResultLike, str]:
    if source_only is None:
        source_only = bool(marker and marker.startswith(REVIEWED_SOURCES_ONLY_MARKER))
    if source_only:
        if marker is not None and not marker.startswith(REVIEWED_SOURCES_ONLY_MARKER):
            raise ProviderContextChanged
        return _current_sources_only_result(
            session,
            scope,
            observed_at,
            settings,
            marker.removeprefix(REVIEWED_SOURCES_ONLY_MARKER) if marker else None,
        )
    if marker is not None and marker.startswith(REVIEWED_SOURCES_ONLY_MARKER):
        raise ProviderContextChanged
    if include_sources is None:
        include_sources = bool(marker and marker.startswith(REVIEWED_SOURCES_MARKER))
    if marker is not None:
        if marker.startswith(REVIEWED_SOURCES_MARKER) != include_sources:
            raise ProviderContextChanged
        marker = marker.removeprefix(REVIEWED_SOURCES_MARKER)

    def response(
        *, selection: ReviewedNoteSelection | None = None, **fields: Any
    ) -> ProviderQuestionResult:
        if not include_sources:
            return ProviderQuestionResult(**fields)
        return ProviderQuestionSourcesResult(
            **fields,
            reviewed_sources=[
                ProviderReviewedSource(
                    source_ref=source.source_ref,
                    source_version=source.source_version,
                    source_at=source.source_at,
                    sections=source.content,
                )
                for source in selection.sources
            ]
            if selection is not None
            else [],
        )

    def response_marker(value: str) -> str:
        return REVIEWED_SOURCES_MARKER + value if include_sources else value

    selection, digest = current_source_state(
        session,
        scope,
        observed_at=observed_at,
        settings=settings,
        lock_capture_sources=True,
    )
    artifact = ready_artifact(session, scope.id, digest)
    # A receipt always describes its original read. Publication cannot upgrade
    # a pending receipt into permission to finalize a model-generated answer.
    if marker is not None and marker.startswith("ready:"):
        if artifact is None or marker != f"ready:{artifact.id}":
            raise ProviderContextChanged
    if artifact is not None and (marker is None or marker.startswith("ready:")):
        stored = artifact_result(artifact)
        if artifact.source_manifest != source_manifest(selection):
            raise SessionPrepInvariantError("Preparation source evidence is invalid")
        return response(
            selection=selection,
            state="ready",
            reason_code=None,
            source_version_digest=f"sha256_{digest}",
            prepared_brief=stored.prepared_brief,
            provenance=[p for p in stored.provenance if p.included],
            completeness=stored.completeness,
            current_through=stored.current_through,
            generated_at=stored.brief_generated_at,
        ), response_marker(f"ready:{artifact.id}")
    reason = "no_eligible_sources" if not selection.sources else "context_not_available"
    if marker is None and selection.sources:
        build = session.exec(
            select(SessionPrepBuild).where(SessionPrepBuild.record_scope_id == scope.id)
        ).one_or_none()
        if (
            build is not None
            and build.desired_digest == digest
            and build.state in {"pending", "running"}
        ):
            reason = "generation_pending"
    if marker is not None:
        parts = marker.split(":")
        if (
            len(parts) != 2
            or parts[1] != digest
            or parts[0]
            not in {
                "generation_pending",
                "no_eligible_sources",
                "context_not_available",
            }
        ):
            raise ProviderContextChanged
        reason = parts[0]
    return response(
        state="pending" if reason == "generation_pending" else "unavailable",
        reason_code=reason,  # type: ignore[arg-type]
        source_version_digest=f"sha256_{digest}",
        prepared_brief=None,
        provenance=[],
        completeness="none",
        current_through=None,
        generated_at=None,
    ), response_marker(f"{reason}:{digest}")


def _current_sources_only_result(
    session: Session,
    scope: ProviderRecordAuthority,
    observed_at: datetime,
    settings: AuthorityKernelSettings,
    marker: str | None,
) -> tuple[ProviderQuestionSourcesOnlyResult, str]:
    """Reconstruct the exact initial source read without scheduling a model.

    The marker pins the initial brief state as well as source digest, so a
    pending read cannot silently acquire a later generated brief on replay.
    """
    selection, digest = current_source_state(
        session,
        scope,
        observed_at=observed_at,
        settings=settings,
        lock_capture_sources=True,
    )
    artifact = ready_artifact(session, scope.id, digest)
    if marker is None:
        if not selection.sources:
            marker = f"no_eligible_sources:{digest}"
        elif artifact is not None:
            marker = f"ready:{artifact.id}:{digest}"
        else:
            build = session.exec(
                select(SessionPrepBuild).where(
                    SessionPrepBuild.record_scope_id == scope.id
                )
            ).one_or_none()
            pending = (
                build is not None
                and build.desired_digest == digest
                and build.state in {"pending", "running"}
            )
            marker = f"{'brief_pending' if pending else 'brief_unavailable'}:{digest}"
    parts = marker.split(":")
    if not parts or parts[-1] != digest:
        raise ProviderContextChanged
    prepared, generated_at = None, None
    if parts[0] == "ready":
        if (
            len(parts) != 3
            or artifact is None
            or parts[1] != str(artifact.id)
            or not selection.sources
        ):
            raise ProviderContextChanged
        stored = artifact_result(artifact)
        if artifact.source_manifest != source_manifest(selection):
            raise SessionPrepInvariantError("Preparation source evidence is invalid")
        prepared, generated_at = stored.prepared_brief, stored.brief_generated_at
        brief_state = "ready"
    elif len(parts) == 2 and parts[0] in {
        "brief_pending",
        "brief_unavailable",
        "no_eligible_sources",
    }:
        if (parts[0] == "no_eligible_sources") != (not selection.sources):
            raise ProviderContextChanged
        brief_state = "pending" if parts[0] == "brief_pending" else "unavailable"
    else:
        raise ProviderContextChanged
    return ProviderQuestionSourcesOnlyResult(
        state="ready" if selection.sources else "unavailable",
        reason_code=None if selection.sources else "no_eligible_sources",
        source_version_digest=f"sha256_{digest}",
        brief_state=brief_state,
        prepared_brief=prepared,
        provenance=[
            SessionPrepProvenance(
                source_class="clinician_finalized_note",
                included=True,
                source_ref=source.source_ref,
                source_version=source.source_version,
                source_at=source.source_at,
                author_class="clinician",
                reason_code="eligible_finalized_note",
            )
            for source in selection.sources
        ],
        completeness=("partial" if selection.truncated else "complete")
        if selection.sources
        else "none",
        current_through=max(
            (source.source_at for source in selection.sources), default=None
        ),
        generated_at=generated_at,
        reviewed_sources=[
            ProviderReviewedSource(
                source_ref=source.source_ref,
                source_version=source.source_version,
                source_at=source.source_at,
                sections=source.content,
            )
            for source in selection.sources
        ],
    ), REVIEWED_SOURCES_ONLY_MARKER + marker


def read_provider_question_context(
    *,
    session: Session,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    idempotency_key: str,
    invocation: ProviderQuestionInvocation,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> ProviderQuestionRead:
    authorized = authority.authorize_interaction(
        session=session,
        workload_client_id=workload_client_id,
        mount_context_token=mount_context_token,
        interaction_ref=interaction_ref,
        client_turn_id=client_turn_id,
        settings=settings,
    )
    turn_ref = invocation.arguments.source_turn_ref
    scope = _turn_and_scope(session, authorized, turn_ref)
    now = datetime.now(UTC)
    receipt = _read_receipt(session, authorized)
    replayed = receipt is not None
    if receipt is not None:
        expiry = (
            receipt.retain_until.replace(tzinfo=UTC)
            if receipt.retain_until.tzinfo is None
            else receipt.retain_until
        )
        if receipt.idempotency_key != idempotency_key:
            raise SessionPrepIdempotencyConflict
        if (
            receipt.logical_ref != turn_ref
            or receipt.status != "complete"
            or expiry <= now
            or receipt.response_ref is None
        ):
            raise ProviderContextChanged
    elif (
        session.exec(
            select(CanonicalConversationFinalPhase).where(
                CanonicalConversationFinalPhase.turn_id == turn_ref
            )
        ).one_or_none()
        is not None
    ):
        raise SessionPrepScopeDenied
    result, marker = _current_result(
        session,
        scope,
        now,
        settings,
        receipt.response_ref if receipt else None,
        include_sources=invocation.tool_version != "1.0.0",
        source_only=invocation.tool_version == "1.2.0",
    )
    request_digest = _binding_digest(turn_ref, result)
    if receipt is not None and receipt.request_digest != request_digest:
        raise ProviderContextChanged
    if receipt is None:
        receipt = AuthorityIdempotencyRecord(
            workload_client_id=workload_client_id,
            operation=OPERATION,
            interaction_id=authorized.lineage.id,
            logical_ref=turn_ref,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            status="complete",
            response_ref=marker,
            retain_until=now + RETENTION,
        )
        session.add(receipt)
        session.flush()
        session.add(
            AuthorityAuditEvent(
                event_type=OPERATION,
                outcome="accepted",
                occurred_at=now,
                tenant_id=authorized.mount_session.tenant_id,
                actor_id=authorized.mount_session.actor_id,
                person_id=authorized.context.person_id,
                interaction_id=authorized.lineage.id,
                resource_type="provider_question_context",
                resource_ref=str(receipt.id),
                request_digest=request_digest,
            )
        )
    response = ProviderQuestionResponse(tool_call_ref=str(receipt.id), result=result)
    session.commit()
    return ProviderQuestionRead(response, replayed)


def validate_final_context(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
    payload: dict[str, Any],
    settings: AuthorityKernelSettings,
) -> None:
    """Runs under the final writer's authority locks and commit, including replay."""
    receipt = _read_receipt(session, authorized)
    reference = payload.get("provider_context_ref")
    if receipt is None:
        if reference is not None:
            raise ProviderContextChanged
        return  # Legacy final writes have no additional read dependency.
    now = datetime.now(UTC)
    expiry = (
        receipt.retain_until.replace(tzinfo=UTC)
        if receipt.retain_until.tzinfo is None
        else receipt.retain_until
    )
    if (
        reference != str(receipt.id)
        or receipt.status != "complete"
        or receipt.response_ref is None
        or expiry <= now
    ):
        raise ProviderContextChanged
    scope = _turn_and_scope(session, authorized, receipt.logical_ref)
    try:
        _lock_final_sources(session, scope, receipt.response_ref, now)
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        # NOWAIT avoids Doctor -> Appointment waiting against a background
        # Appointment -> Doctor-FK writer. Release every authority/source lock
        # and leave no aborted transaction before returning the refusal.
        session.rollback()
        raise ProviderContextChanged from None
    result, _ = _current_result(
        session,
        scope,
        now,
        settings,
        receipt.response_ref,
    )
    if receipt.request_digest != _binding_digest(receipt.logical_ref, result):
        raise ProviderContextChanged
    if result.state != "ready" and payload["outcome"] != "unavailable":
        raise ProviderContextChanged


def _lock_final_sources(
    session: Session, scope: ProviderRecordAuthority, marker: str, observed_at: datetime
) -> None:
    """Retain Appointment -> Note source locks until final commit, without waiting.

    Authority already holds Doctor -> token -> lineage. Background summarize
    and note restore can publish without Doctor, so its lock alone is not a
    source fence. Discover the bounded immutable artifact references, lock all
    involved appointments before notes, then re-read sources under those locks.
    NOWAIT is essential: ordinary writers may already hold Appointment before
    checking a new note's Doctor FK. A busy source refuses this final instead
    of waiting with the authority Doctor lock and forming a deadlock.
    No model/network operation runs in this transaction.
    """
    note_refs: list[str] = []
    if marker.startswith(REVIEWED_SOURCES_ONLY_MARKER):
        selection = load_reviewed_note_sources(
            session=session,
            doctor_id=scope.doctor_id,
            target_appointment_id=scope.appointment_id,
            observed_at=observed_at,
        )
        if marker.split(":")[-1] != source_digest(selection):
            raise ProviderContextChanged
        note_refs = [source.source_ref for source in selection.sources]
    else:
        marker = marker.removeprefix(REVIEWED_SOURCES_MARKER)
    if not marker.startswith(REVIEWED_SOURCES_ONLY_MARKER) and marker.startswith(
        "ready:"
    ):
        artifact = session.exec(
            select(SessionPrepArtifact).where(
                SessionPrepArtifact.id == marker.removeprefix("ready:"),
                SessionPrepArtifact.record_scope_id == scope.id,
            )
        ).one_or_none()
        if artifact is None or not 1 <= len(artifact.source_manifest) <= 5:
            raise ProviderContextChanged
        note_refs = [source["source_ref"] for source in artifact.source_manifest]
    try:
        lock_note_source_versions(session, scope, note_refs, observed_at)
    except NoteSourceScopeChanged:
        raise ProviderContextChanged from None
