"""Synthetic-only Session Prep artifact pipeline.

The module deliberately has no patient-model, EHR, transcript, or model-provider
dependency.  It exercises the ratified Core-owned artifact lifecycle with fixed
non-PHI inputs while real source adapters, durable storage, and activation remain
separate gated work.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Callable, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import app.services.authority as authority
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)

from activemodel.session_manager import get_session
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from sqlalchemy.exc import StatementError
from sqlmodel import Session, select

OpaqueRef = Annotated[
    str,
    Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._~-]{8,160}$"),
]
LineageVersion = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"),
]
SourceClass = Literal["clinician_finalized_note", "patient_reported_visit_prep"]
AuthorClass = Literal["clinician", "patient"]
ResultState = Literal["ready", "refreshing", "pending", "unavailable"]
ResultDetail = Literal["brief", "supporting_context"]
ProvenanceReason = Literal[
    "eligible_finalized_note",
    "eligible_patient_visit_prep",
    "no_eligible_source",
    "source_not_enabled",
    "selection_limit",
    "source_temporarily_unavailable",
]

MAX_FINALIZED_NOTES = 5
MAX_NOTE_CHARACTERS = 18_000
MAX_GENERATOR_CHARACTERS = 96_000
MAX_RETURNED_CONTEXT_CHARACTERS = 8_000
MAX_STALE_AGE = timedelta(minutes=15)
GENERATOR_VERSION = "session_prep_generator_v1"
PROMPT_VERSION = "session_prep_prompt_v1"
SESSION_PREP_OPEN_ACTION = "session-prep.open"


class SessionPrepInvariantError(RuntimeError):
    pass


class SessionPrepIdempotencyConflict(SessionPrepInvariantError):
    pass


class SessionPrepScopeDenied(SessionPrepInvariantError):
    pass


class SessionPrepSourceBoundExceeded(SessionPrepInvariantError):
    pass


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("A timezone is required.")
    return value


def _unique_refs(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("Source references must be unique.")
    return values


class SessionPrepBriefText(_ClosedModel):
    text: str = Field(min_length=1, max_length=1000)
    source_refs: list[OpaqueRef] = Field(min_length=1, max_length=6)

    _refs_are_unique = field_validator("source_refs")(_unique_refs)


class SessionPrepRecap(_ClosedModel):
    text: str = Field(min_length=1, max_length=4000)
    source_refs: list[OpaqueRef] = Field(min_length=1, max_length=6)

    _refs_are_unique = field_validator("source_refs")(_unique_refs)


class SessionPrepPatientReported(_ClosedModel):
    label: Literal["Patient-reported Visit Prep"]
    summary: str = Field(min_length=1, max_length=2000)
    source_refs: list[OpaqueRef] = Field(min_length=1, max_length=6)

    _refs_are_unique = field_validator("source_refs")(_unique_refs)


class SessionPrepPreparedBrief(_ClosedModel):
    recap: SessionPrepRecap
    trajectory_and_material_changes: list[SessionPrepBriefText] = Field(max_length=8)
    talking_points: list[SessionPrepBriefText] = Field(min_length=1, max_length=8)
    open_threads: list[SessionPrepBriefText] = Field(max_length=8)
    cautions: list[SessionPrepBriefText] = Field(max_length=8)
    suggested_outline: list[SessionPrepBriefText] = Field(min_length=1, max_length=8)
    patient_reported: SessionPrepPatientReported | None


class SessionPrepProvenance(_ClosedModel):
    source_class: SourceClass
    included: bool
    source_ref: OpaqueRef | None
    source_version: int | None = Field(default=None, ge=1, le=2_147_483_647)
    source_at: datetime | None
    author_class: AuthorClass | None
    reason_code: ProvenanceReason

    _source_at_is_aware = field_validator("source_at")(_aware)

    @model_validator(mode="after")
    def included_fields_are_consistent(self) -> SessionPrepProvenance:
        populated = (
            self.source_ref is not None
            and self.source_version is not None
            and self.source_at is not None
            and self.author_class is not None
        )
        if self.included != populated:
            raise ValueError("Included provenance must carry complete source lineage.")
        if self.included:
            expected = (
                ("clinician", "eligible_finalized_note")
                if self.source_class == "clinician_finalized_note"
                else ("patient", "eligible_patient_visit_prep")
            )
            if (self.author_class, self.reason_code) != expected:
                raise ValueError("Included provenance has inconsistent source lineage.")
        elif self.reason_code.startswith("eligible_"):
            raise ValueError("Excluded provenance cannot be eligible.")
        if (
            self.source_class == "clinician_finalized_note"
            and not self.included
            and self.reason_code == "source_not_enabled"
        ):
            raise ValueError("The finalized-note source class is enabled.")
        return self


class SessionPrepExcerpt(_ClosedModel):
    section: Literal[
        "history",
        "assessment",
        "plan",
        "medication",
        "safety",
        "patient_concern",
        "other",
    ]
    text: str = Field(min_length=1, max_length=2000)


class SessionPrepSupportingContext(_ClosedModel):
    source_ref: OpaqueRef
    source_class: SourceClass
    author_class: AuthorClass
    source_version: int = Field(ge=1, le=2_147_483_647)
    source_at: datetime
    summary: str = Field(min_length=1, max_length=2000)
    excerpts: list[SessionPrepExcerpt] = Field(min_length=1, max_length=8)

    _source_at_is_aware = field_validator("source_at")(_aware)


class SessionPrepResult(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    state: ResultState
    detail: ResultDetail
    freshness: Literal["current", "stale", "none"]
    completeness: Literal["complete", "partial", "none"]
    current_through: datetime | None
    snapshot_ref: OpaqueRef | None
    snapshot_version: int | None = Field(default=None, ge=1, le=2_147_483_647)
    snapshot_generated_at: datetime | None
    snapshot_supersedes_ref: OpaqueRef | None
    source_version_digest: str | None = Field(
        default=None,
        min_length=71,
        max_length=71,
        pattern=r"^sha256_[a-f0-9]{64}$",
    )
    brief_ref: OpaqueRef | None
    brief_version: int | None = Field(default=None, ge=1, le=2_147_483_647)
    brief_generated_at: datetime | None
    brief_supersedes_ref: OpaqueRef | None
    brief_generator_version: LineageVersion | None
    brief_prompt_version: LineageVersion | None
    brief_model_profile: LineageVersion | None
    source_count: int = Field(ge=0, le=6)
    provenance: list[SessionPrepProvenance] = Field(max_length=8)
    prepared_brief: SessionPrepPreparedBrief | None
    supporting_context: list[SessionPrepSupportingContext] | None = Field(
        default=None,
        min_length=1,
        max_length=6,
    )
    retry_after_seconds: int | None = Field(default=None, ge=1, le=300)
    reason_code: (
        Literal[
            "refresh_in_progress",
            "generation_pending",
            "no_eligible_sources",
            "context_not_available",
        ]
        | None
    )

    _current_through_is_aware = field_validator("current_through")(_aware)
    _snapshot_generated_at_is_aware = field_validator("snapshot_generated_at")(_aware)
    _brief_generated_at_is_aware = field_validator("brief_generated_at")(_aware)

    @model_validator(mode="after")
    def enforce_contract_relationships(self) -> SessionPrepResult:
        content_fields = (
            self.current_through,
            self.snapshot_ref,
            self.snapshot_version,
            self.snapshot_generated_at,
            self.source_version_digest,
            self.brief_ref,
            self.brief_version,
            self.brief_generated_at,
            self.brief_generator_version,
            self.brief_prompt_version,
            self.prepared_brief,
        )
        if self.state in {"pending", "unavailable"}:
            if (
                any(value is not None for value in content_fields)
                or self.freshness != "none"
                or self.completeness != "none"
                or self.snapshot_supersedes_ref is not None
                or self.brief_supersedes_ref is not None
                or self.brief_model_profile is not None
                or self.source_count != 0
                or self.provenance
                or self.supporting_context is not None
            ):
                raise ValueError("Content-free states cannot carry artifact lineage.")
            if self.state == "pending" and (
                self.reason_code != "generation_pending"
                or self.retry_after_seconds is None
            ):
                raise ValueError("Pending results require a bounded retry.")
            if self.state == "unavailable" and (
                self.reason_code not in {"no_eligible_sources", "context_not_available"}
                or self.retry_after_seconds is not None
            ):
                raise ValueError("Unavailable results are non-retryable.")
            return self

        if any(value is None for value in content_fields):
            raise ValueError(
                "Content-bearing states require complete artifact lineage."
            )
        if self.state == "ready" and (
            self.freshness != "current"
            or self.reason_code is not None
            or self.retry_after_seconds is not None
        ):
            raise ValueError("Ready results must be current and final.")
        if self.state == "refreshing" and (
            self.freshness != "stale"
            or self.reason_code != "refresh_in_progress"
            or self.retry_after_seconds is None
        ):
            raise ValueError("Refreshing results require stale content and a retry.")
        if self.completeness not in {"complete", "partial"}:
            raise ValueError("Content-bearing results need bounded completeness.")
        if self.detail == "brief" and self.supporting_context is not None:
            raise ValueError("Brief reads cannot include supporting context.")
        if self.detail == "supporting_context" and not self.supporting_context:
            raise ValueError("Explicit supporting-context reads require content.")

        included = [item for item in self.provenance if item.included]
        included_refs = {cast(str, item.source_ref) for item in included}
        if len(included_refs) != len(included) or self.source_count != len(included):
            raise ValueError("Included provenance must be unique and counted exactly.")
        if self.completeness == "complete" and any(
            item.reason_code in {"selection_limit", "source_temporarily_unavailable"}
            for item in self.provenance
        ):
            raise ValueError(
                "A complete result cannot omit otherwise eligible content."
            )
        if self.completeness == "partial" and not any(
            item.reason_code in {"selection_limit", "source_temporarily_unavailable"}
            for item in self.provenance
        ):
            raise ValueError("A partial result must explain its omission.")

        brief = cast(SessionPrepPreparedBrief, self.prepared_brief)
        cited = set(brief.recap.source_refs)
        for section in (
            brief.trajectory_and_material_changes,
            brief.talking_points,
            brief.open_threads,
            brief.cautions,
            brief.suggested_outline,
        ):
            for item in section:
                cited.update(item.source_refs)
        if brief.patient_reported is not None:
            cited.update(brief.patient_reported.source_refs)
            patient_refs = {
                cast(str, item.source_ref)
                for item in included
                if item.source_class == "patient_reported_visit_prep"
            }
            if not set(brief.patient_reported.source_refs) <= patient_refs:
                raise ValueError("Patient-reported content needs patient provenance.")
        if not cited <= included_refs:
            raise ValueError(
                "Every brief citation must resolve to included provenance."
            )

        provenance_by_ref = {cast(str, item.source_ref): item for item in included}
        context_chars = 0
        for item in self.supporting_context or []:
            source = provenance_by_ref.get(item.source_ref)
            if source is None or (
                item.source_class,
                item.author_class,
                item.source_version,
                item.source_at,
            ) != (
                source.source_class,
                source.author_class,
                source.source_version,
                source.source_at,
            ):
                raise ValueError("Supporting context must match source provenance.")
            context_chars += len(item.summary) + sum(
                len(excerpt.text) for excerpt in item.excerpts
            )
        if context_chars > MAX_RETURNED_CONTEXT_CHARACTERS:
            raise ValueError("Supporting context exceeds its aggregate bound.")

        current_through = cast(datetime, self.current_through)
        snapshot_at = cast(datetime, self.snapshot_generated_at)
        brief_at = cast(datetime, self.brief_generated_at)
        if not current_through <= snapshot_at <= brief_at:
            raise ValueError("Artifact timestamps are not monotonic.")
        if any(cast(datetime, item.source_at) > current_through for item in included):
            raise ValueError("Sources cannot exceed the snapshot cutoff.")
        if (self.snapshot_version == 1) != (self.snapshot_supersedes_ref is None):
            raise ValueError("Snapshot predecessor lineage is inconsistent.")
        if (self.brief_version == 1) != (self.brief_supersedes_ref is None):
            raise ValueError("Brief predecessor lineage is inconsistent.")
        return self


class SessionPrepToolResponse(_ClosedModel):
    tool_call_ref: OpaqueRef
    tool_name: Literal["session-prep.context.read"]
    tool_version: Literal["1.0.0"]
    status: Literal["completed"]
    approval_ref: None
    access_intent_ref: None
    access_outcome_ref: None
    result_classification: Literal["phi_possible"]
    result: SessionPrepResult
    recorded_at: datetime

    _recorded_at_is_aware = field_validator("recorded_at")(_aware)


@dataclass(frozen=True)
class FinalizedNoteCandidate:
    """One fixed synthetic note revision and its immutable finalization evidence."""

    source_ref: str
    source_version: int
    source_at: datetime
    lifecycle: Literal["finalized", "draft", "raw_transcript", "deleted"]
    clinician_reviewed: bool
    clinician_action_ref: str | None
    content_sha256: str
    recap: str
    material_changes: tuple[str, ...]
    talking_points: tuple[str, ...]
    open_threads: tuple[str, ...]
    cautions: tuple[str, ...]
    suggested_outline: tuple[str, ...]
    supporting_summary: str
    supporting_excerpts: tuple[tuple[str, str], ...]

    def canonical_content(self) -> str:
        return json.dumps(
            {
                "recap": self.recap,
                "material_changes": self.material_changes,
                "talking_points": self.talking_points,
                "open_threads": self.open_threads,
                "cautions": self.cautions,
                "suggested_outline": self.suggested_outline,
                "supporting_summary": self.supporting_summary,
                "supporting_excerpts": self.supporting_excerpts,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True)
class SyntheticContext:
    context_snapshot_ref: str
    target_encounter_at: datetime
    state: Literal["ready", "refreshing", "pending", "source_revoked"]
    notes: tuple[FinalizedNoteCandidate, ...]
    temporarily_unavailable: bool = False


@dataclass(frozen=True)
class ImmutableSnapshot:
    snapshot_ref: str
    source_version_digest: str
    generated_at: datetime
    current_through: datetime
    notes: tuple[FinalizedNoteCandidate, ...]
    completeness: Literal["complete", "partial"]
    provenance: tuple[SessionPrepProvenance, ...]


@dataclass(frozen=True)
class ImmutableBrief:
    brief_ref: str
    generated_at: datetime
    prepared_brief: SessionPrepPreparedBrief
    supporting_context: tuple[SessionPrepSupportingContext, ...]


@dataclass(frozen=True)
class SessionPrepInvocation:
    response: SessionPrepToolResponse
    replayed: bool


@dataclass(frozen=True)
class SessionPrepAuthorization:
    authorized: authority.AuthorizedInteraction
    turn_closed: bool


@dataclass(frozen=True)
class _CachedInvocation:
    request_digest: str
    response: SessionPrepToolResponse


class ImmutableArtifactStore:
    """Process-local synthetic store that rejects replacement of immutable refs."""

    def __init__(self) -> None:
        self._snapshots: dict[str, ImmutableSnapshot] = {}
        self._briefs: dict[str, ImmutableBrief] = {}
        self._lock = threading.Lock()

    def record(
        self,
        snapshot: ImmutableSnapshot,
        brief: ImmutableBrief,
    ) -> tuple[ImmutableSnapshot, ImmutableBrief]:
        with self._lock:
            prior_snapshot = self._snapshots.setdefault(snapshot.snapshot_ref, snapshot)
            prior_brief = self._briefs.setdefault(brief.brief_ref, brief)
            if prior_snapshot != snapshot or prior_brief != brief:
                raise SessionPrepInvariantError(
                    "immutable artifact replacement refused"
                )
            return prior_snapshot, prior_brief


def authorize_session_prep_read(
    *,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    source_turn_ref: str,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
    session: Session | None = None,
) -> SessionPrepAuthorization:
    """Consume current platform authority without changing its grant behavior."""

    if session is None:
        with get_session() as managed_session:
            return authorize_session_prep_read(
                workload_client_id=workload_client_id,
                mount_context_token=mount_context_token,
                interaction_ref=interaction_ref,
                client_turn_id=client_turn_id,
                source_turn_ref=source_turn_ref,
                settings=settings,
                session=cast(Session, managed_session),
            )
    authorized = authority.authorize_interaction(
        session=session,
        workload_client_id=workload_client_id,
        mount_context_token=mount_context_token,
        interaction_ref=interaction_ref,
        client_turn_id=client_turn_id,
        settings=settings,
    )
    if "session-prep:read" not in authorized.context.capabilities:
        raise authority.AuthorityRequestDeniedError
    try:
        turn = session.get(CanonicalConversationTurn, source_turn_ref)
    except (TypeError, ValueError, StatementError):
        raise SessionPrepScopeDenied from None
    if turn is None:
        raise SessionPrepScopeDenied
    user_phase = session.exec(
        select(CanonicalConversationUserPhase).where(
            CanonicalConversationUserPhase.turn_id == turn.id
        )
    ).one_or_none()
    final_phase = session.exec(
        select(CanonicalConversationFinalPhase).where(
            CanonicalConversationFinalPhase.turn_id == turn.id
        )
    ).one_or_none()
    if (
        turn.interaction_id != authorized.lineage.id
        or turn.workload_client_id != workload_client_id
        or turn.client_turn_id != client_turn_id
        or turn.tenant_id != authorized.mount_session.tenant_id
        or turn.actor_id != authorized.mount_session.actor_id
        or turn.person_id != authorized.context.person_id
        or user_phase is None
        or user_phase.ingress_kind != "provider_ui_action"
        or user_phase.action_code != SESSION_PREP_OPEN_ACTION
    ):
        raise SessionPrepScopeDenied
    return SessionPrepAuthorization(
        authorized=authorized,
        turn_closed=final_phase is not None,
    )


def _hash_json(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _clip(value: str, limit: int) -> str:
    clipped = value.strip()[:limit].strip()
    return clipped or "Synthetic source content is available for review."


def _eligible_finalized_note(
    note: FinalizedNoteCandidate,
    *,
    target_encounter_at: datetime,
) -> bool:
    canonical = note.canonical_content()
    return bool(
        note.lifecycle == "finalized"
        and note.clinician_reviewed
        and note.clinician_action_ref
        and note.content_sha256
        == f"sha256_{hashlib.sha256(canonical.encode()).hexdigest()}"
        and target_encounter_at - timedelta(days=548)
        <= note.source_at
        < target_encounter_at
    )


def _select_notes(
    context: SyntheticContext,
    *,
    generated_at: datetime,
) -> ImmutableSnapshot | None:
    eligible = sorted(
        (
            note
            for note in context.notes
            if _eligible_finalized_note(
                note,
                target_encounter_at=context.target_encounter_at,
            )
        ),
        key=lambda note: (note.source_at, note.source_ref, note.source_version),
        reverse=True,
    )
    selected: list[FinalizedNoteCandidate] = []
    generator_characters = 0
    for note in eligible:
        characters = len(note.canonical_content())
        if (
            characters > MAX_NOTE_CHARACTERS
            or len(selected) == MAX_FINALIZED_NOTES
            or generator_characters + characters > MAX_GENERATOR_CHARACTERS
        ):
            continue
        selected.append(note)
        generator_characters += characters
    if not selected:
        if eligible:
            raise SessionPrepSourceBoundExceeded
        return None

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
        for note in selected
    ]
    partial = len(selected) < len(eligible) or context.temporarily_unavailable
    if len(selected) < len(eligible):
        provenance.append(
            SessionPrepProvenance(
                source_class="clinician_finalized_note",
                included=False,
                source_ref=None,
                source_version=None,
                source_at=None,
                author_class=None,
                reason_code="selection_limit",
            )
        )
    elif context.temporarily_unavailable:
        provenance.append(
            SessionPrepProvenance(
                source_class="clinician_finalized_note",
                included=False,
                source_ref=None,
                source_version=None,
                source_at=None,
                author_class=None,
                reason_code="source_temporarily_unavailable",
            )
        )
    provenance.append(
        SessionPrepProvenance(
            source_class="patient_reported_visit_prep",
            included=False,
            source_ref=None,
            source_version=None,
            source_at=None,
            author_class=None,
            reason_code="source_not_enabled",
        )
    )

    projection = [
        {
            "source_ref": note.source_ref,
            "source_version": note.source_version,
            "content_sha256": note.content_sha256,
        }
        for note in selected
    ]
    digest = f"sha256_{_hash_json(projection)}"
    snapshot_ref = f"snapshot_{_hash_json([context.context_snapshot_ref, digest])[:32]}"
    current_through = min(context.target_encounter_at, generated_at)
    if any(note.source_at > current_through for note in selected):
        raise SessionPrepInvariantError("synthetic source exceeds current cutoff")
    return ImmutableSnapshot(
        snapshot_ref=snapshot_ref,
        source_version_digest=digest,
        generated_at=generated_at,
        current_through=current_through,
        notes=tuple(selected),
        completeness="partial" if partial else "complete",
        provenance=tuple(provenance),
    )


def _brief_items(
    notes: tuple[FinalizedNoteCandidate, ...],
    field_name: Literal[
        "material_changes",
        "talking_points",
        "open_threads",
        "cautions",
        "suggested_outline",
    ],
) -> list[SessionPrepBriefText]:
    items: list[SessionPrepBriefText] = []
    seen: set[str] = set()
    for note in notes:
        for text in getattr(note, field_name):
            bounded = _clip(text, 1000)
            if bounded in seen:
                continue
            seen.add(bounded)
            items.append(
                SessionPrepBriefText(text=bounded, source_refs=[note.source_ref])
            )
            if len(items) == 8:
                return items
    return items


def _generate_brief(
    snapshot: ImmutableSnapshot,
    *,
    generated_at: datetime,
) -> ImmutableBrief:
    notes = snapshot.notes
    talking_points = _brief_items(notes, "talking_points") or [
        SessionPrepBriefText(
            text="Confirm interval changes and the prior documented plan.",
            source_refs=[notes[0].source_ref],
        )
    ]
    suggested_outline = _brief_items(notes, "suggested_outline") or [
        SessionPrepBriefText(
            text="Review the prior plan and verify current status.",
            source_refs=[notes[0].source_ref],
        )
    ]
    recap_text = " ".join(note.recap for note in notes[:3])
    prepared = SessionPrepPreparedBrief(
        recap=SessionPrepRecap(
            text=_clip(recap_text, 4000),
            source_refs=[note.source_ref for note in notes[:3]],
        ),
        trajectory_and_material_changes=_brief_items(notes, "material_changes"),
        talking_points=talking_points,
        open_threads=_brief_items(notes, "open_threads"),
        cautions=_brief_items(notes, "cautions"),
        suggested_outline=suggested_outline,
        patient_reported=None,
    )
    brief_ref = f"brief_{_hash_json([snapshot.snapshot_ref, GENERATOR_VERSION, PROMPT_VERSION])[:32]}"
    return ImmutableBrief(
        brief_ref=brief_ref,
        generated_at=generated_at,
        prepared_brief=prepared,
        supporting_context=(),
    )


_PROHIBITED_EGRESS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:diagnosis\s+is|diagnos(?:e|ed)\s+(?:as|with)|(?:new|confirmed|definitive)\s+diagnosis|this\s+confirms?\s+(?:a\s+|the\s+)?diagnosis)\b",
        r"(?:^|[.!?]\s*)(?:please\s+)?(?:start|stop|increase|decrease)\b(?!\s+(?:with|by|the\s+(?:visit|session|discussion|review)))",
        r"\b(?:start|stop|increase|decrease)\b[^.!?\n]{0,80}\b(?:taking|using|dose|dosage|medication|medicine|drug|prescription|mg|mcg|tablet|capsule)s?\b",
        r"\b(?:avoid|delay|defer|postpone|skip|do\s+not\s+seek|don't\s+seek)\b[^.!?\n]{0,80}\b(?:urgent[- ]care|emergency\s+(?:care|department)|ER|911)\b",
    )
)


def _passes_safety(brief: ImmutableBrief) -> bool:
    prepared = brief.prepared_brief
    values = [prepared.recap.text]
    for section in (
        prepared.trajectory_and_material_changes,
        prepared.talking_points,
        prepared.open_threads,
        prepared.cautions,
        prepared.suggested_outline,
    ):
        values.extend(item.text for item in section)
    if prepared.patient_reported is not None:
        values.append(prepared.patient_reported.summary)
    return not any(
        pattern.search(value) for value in values for pattern in _PROHIBITED_EGRESS
    )


def _empty_result(
    *,
    detail: ResultDetail,
    state: Literal["pending", "unavailable"],
    reason_code: Literal[
        "generation_pending", "no_eligible_sources", "context_not_available"
    ],
) -> SessionPrepResult:
    return SessionPrepResult(
        state=state,
        detail=detail,
        freshness="none",
        completeness="none",
        current_through=None,
        snapshot_ref=None,
        snapshot_version=None,
        snapshot_generated_at=None,
        snapshot_supersedes_ref=None,
        source_version_digest=None,
        brief_ref=None,
        brief_version=None,
        brief_generated_at=None,
        brief_supersedes_ref=None,
        brief_generator_version=None,
        brief_prompt_version=None,
        brief_model_profile=None,
        source_count=0,
        provenance=[],
        prepared_brief=None,
        supporting_context=None,
        retry_after_seconds=5 if state == "pending" else None,
        reason_code=reason_code,
    )


class SyntheticSessionPrepService:
    """Exact-ref, process-local implementation for compatibility demonstrations.

    A restart or replica change loses its replay receipts and therefore fails a
    closed turn rather than claiming durable replay. Production stays dark until
    a schema-controlled durable artifact and receipt repository replaces this.
    """

    def __init__(
        self,
        contexts: tuple[SyntheticContext, ...],
        *,
        clock: Callable[[], datetime] | None = None,
        artifact_store: ImmutableArtifactStore | None = None,
    ) -> None:
        self._contexts = {context.context_snapshot_ref: context for context in contexts}
        if len(self._contexts) != len(contexts):
            raise SessionPrepInvariantError("synthetic context refs must be unique")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._artifact_store = artifact_store or ImmutableArtifactStore()
        self._artifacts: dict[str, tuple[ImmutableSnapshot, ImmutableBrief]] = {}
        self._invocations: dict[tuple[str, str, str, str], _CachedInvocation] = {}
        self._lock = threading.Lock()

    def _content_result(
        self,
        context: SyntheticContext,
        *,
        detail: ResultDetail,
        observed_at: datetime,
    ) -> SessionPrepResult:
        artifacts = self._artifacts.get(context.context_snapshot_ref)
        if artifacts is None:
            artifact_at = (
                observed_at - timedelta(minutes=5)
                if context.state == "refreshing"
                else observed_at
            )
            try:
                snapshot = _select_notes(context, generated_at=artifact_at)
            except SessionPrepSourceBoundExceeded:
                return _empty_result(
                    detail=detail,
                    state="unavailable",
                    reason_code="context_not_available",
                )
            if snapshot is None:
                return _empty_result(
                    detail=detail,
                    state="unavailable",
                    reason_code="no_eligible_sources",
                )
            brief = _generate_brief(snapshot, generated_at=artifact_at)
            if not _passes_safety(brief):
                return _empty_result(
                    detail=detail,
                    state="unavailable",
                    reason_code="context_not_available",
                )
            artifacts = self._artifact_store.record(snapshot, brief)
            self._artifacts[context.context_snapshot_ref] = artifacts
        snapshot, brief = artifacts
        if not _passes_safety(brief):
            return _empty_result(
                detail=detail,
                state="unavailable",
                reason_code="context_not_available",
            )
        if (
            context.state == "refreshing"
            and observed_at - brief.generated_at > MAX_STALE_AGE
        ):
            return _empty_result(
                detail=detail,
                state="unavailable",
                reason_code="context_not_available",
            )
        state: Literal["ready", "refreshing"] = (
            "refreshing" if context.state == "refreshing" else "ready"
        )
        return SessionPrepResult(
            state=state,
            detail=detail,
            freshness="stale" if state == "refreshing" else "current",
            completeness=snapshot.completeness,
            current_through=snapshot.current_through,
            snapshot_ref=snapshot.snapshot_ref,
            snapshot_version=1,
            snapshot_generated_at=snapshot.generated_at,
            snapshot_supersedes_ref=None,
            source_version_digest=snapshot.source_version_digest,
            brief_ref=brief.brief_ref,
            brief_version=1,
            brief_generated_at=brief.generated_at,
            brief_supersedes_ref=None,
            brief_generator_version=GENERATOR_VERSION,
            brief_prompt_version=PROMPT_VERSION,
            brief_model_profile=None,
            source_count=len(snapshot.notes),
            provenance=list(snapshot.provenance),
            prepared_brief=brief.prepared_brief,
            supporting_context=(
                list(brief.supporting_context)
                if detail == "supporting_context"
                else None
            ),
            retry_after_seconds=5 if state == "refreshing" else None,
            reason_code="refresh_in_progress" if state == "refreshing" else None,
        )

    def invoke(
        self,
        *,
        workload_client_id: str,
        interaction_ref: str,
        client_turn_id: UUID,
        idempotency_key: str,
        source_turn_ref: str,
        context_snapshot_ref: str,
        detail: ResultDetail,
        allowed_context_snapshot_ref: str,
        turn_closed: bool = False,
    ) -> SessionPrepInvocation:
        if context_snapshot_ref != allowed_context_snapshot_ref:
            raise SessionPrepScopeDenied
        context = self._contexts.get(context_snapshot_ref)
        if context is None:
            raise SessionPrepScopeDenied
        if detail != "brief":
            # Supporting context needs a distinct canonical provider follow-up
            # action. This synthetic rung implements only session-prep.open.
            raise SessionPrepScopeDenied

        request_digest = _hash_json(
            {
                "source_turn_ref": source_turn_ref,
                "context_snapshot_ref": context_snapshot_ref,
                "purpose": "session_prep",
                "detail": detail,
            }
        )
        cache_key = (
            workload_client_id,
            interaction_ref,
            str(client_turn_id),
            idempotency_key,
        )
        with self._lock:
            cached = self._invocations.get(cache_key)
            if cached is not None:
                if cached.request_digest != request_digest:
                    raise SessionPrepIdempotencyConflict
                if (
                    context.state != "source_revoked"
                    or cached.response.result.state == "unavailable"
                ):
                    return SessionPrepInvocation(
                        response=cached.response, replayed=True
                    )
            if turn_closed and context.state != "source_revoked":
                raise SessionPrepScopeDenied

            observed_at = self._clock()
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise SessionPrepInvariantError(
                    "synthetic clock must be timezone-aware"
                )
            if context.state == "pending":
                result = _empty_result(
                    detail=detail,
                    state="pending",
                    reason_code="generation_pending",
                )
            elif context.state == "source_revoked":
                result = _empty_result(
                    detail=detail,
                    state="unavailable",
                    reason_code="context_not_available",
                )
            else:
                result = self._content_result(
                    context,
                    detail=detail,
                    observed_at=observed_at,
                )
            tool_call_ref = f"toolcall_{_hash_json([*cache_key, request_digest])[:32]}"
            response = SessionPrepToolResponse(
                tool_call_ref=tool_call_ref,
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
            self._invocations[cache_key] = _CachedInvocation(
                request_digest=request_digest,
                response=response,
            )
            return SessionPrepInvocation(response=response, replayed=False)


def _synthetic_note(
    *,
    source_ref: str,
    source_version: int,
    source_at: datetime,
    recap: str,
    material_changes: tuple[str, ...] = (),
    talking_points: tuple[str, ...] = (),
    open_threads: tuple[str, ...] = (),
    cautions: tuple[str, ...] = (),
    suggested_outline: tuple[str, ...] = (),
    lifecycle: Literal["finalized", "draft", "raw_transcript", "deleted"] = (
        "finalized"
    ),
    clinician_reviewed: bool = True,
) -> FinalizedNoteCandidate:
    note = FinalizedNoteCandidate(
        source_ref=source_ref,
        source_version=source_version,
        source_at=source_at,
        lifecycle=lifecycle,
        clinician_reviewed=clinician_reviewed,
        clinician_action_ref=(
            f"finalization_{source_ref}" if lifecycle == "finalized" else None
        ),
        content_sha256="",
        recap=recap,
        material_changes=material_changes,
        talking_points=talking_points,
        open_threads=open_threads,
        cautions=cautions,
        suggested_outline=suggested_outline,
        supporting_summary="Synthetic clinician-reviewed note summary.",
        supporting_excerpts=(("plan", "Synthetic finalized plan for review."),),
    )
    return replace(
        note,
        content_sha256=(
            f"sha256_{hashlib.sha256(note.canonical_content().encode()).hexdigest()}"
        ),
    )


_TARGET_AT = datetime(2026, 9, 3, 14, 0, tzinfo=UTC)
_READY_NOTE = _synthetic_note(
    source_ref="note_synthetic_001",
    source_version=1,
    source_at=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
    recap="A synthetic finalized note is available for review.",
    talking_points=("Confirm the prior plan and any interval changes.",),
    open_threads=("Verify progress against the prior documented plan.",),
    suggested_outline=("Review progress against the documented prior plan.",),
)
_SECOND_NOTE = _synthetic_note(
    source_ref="note_synthetic_002",
    source_version=1,
    source_at=datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
    recap="A second synthetic finalized note adds longitudinal context.",
    material_changes=("A synthetic interval change was documented.",),
    talking_points=("Confirm whether the documented interval change persists.",),
    cautions=("Verify all synthetic history before relying on it.",),
    suggested_outline=("Compare current status with the prior finalized note.",),
)
_DRAFT_NOTE = _synthetic_note(
    source_ref="note_synthetic_draft_001",
    source_version=1,
    source_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
    recap="Ineligible synthetic draft content must never appear.",
    lifecycle="draft",
    clinician_reviewed=False,
)

SYNTHETIC_CONTEXTS = (
    SyntheticContext(
        context_snapshot_ref="context_synthetic_001",
        target_encounter_at=_TARGET_AT,
        state="ready",
        notes=(_READY_NOTE, _DRAFT_NOTE),
    ),
    SyntheticContext(
        context_snapshot_ref="context_synthetic_002",
        target_encounter_at=_TARGET_AT,
        state="ready",
        notes=(_SECOND_NOTE, _READY_NOTE, _DRAFT_NOTE),
    ),
    SyntheticContext(
        context_snapshot_ref="context_synthetic_refreshing_001",
        target_encounter_at=_TARGET_AT,
        state="refreshing",
        notes=(_SECOND_NOTE,),
        temporarily_unavailable=True,
    ),
    SyntheticContext(
        context_snapshot_ref="context_synthetic_pending_001",
        target_encounter_at=_TARGET_AT,
        state="pending",
        notes=(),
    ),
    SyntheticContext(
        context_snapshot_ref="context_synthetic_revoked_001",
        target_encounter_at=_TARGET_AT,
        state="source_revoked",
        notes=(_READY_NOTE,),
    ),
    SyntheticContext(
        context_snapshot_ref="context_synthetic_empty_001",
        target_encounter_at=_TARGET_AT,
        state="ready",
        notes=(_DRAFT_NOTE,),
    ),
)

SYNTHETIC_SESSION_PREP_SERVICE = SyntheticSessionPrepService(SYNTHETIC_CONTEXTS)
