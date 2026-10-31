"""Closed, immutable citation identities; selection never supplies a URL or note ID."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

MAX_BINDING_BYTES = 32 * 1024
REF_PATTERN = r"^[A-Za-z0-9._~-]{8,160}$"
BACKGROUND = frozenset(
    {
        "patient",
        "mrn / dob",
        "provider",
        "payer",
        "date of service",
        "modality",
        "sites",
        "place of service",
        "telehealth attestation",
        "pmh / psh",
        "allergies",
        "family hx",
        "social hx",
        "immunizations",
        "coding, attestation, signature",
    }
)
SELECTED_PREFIX = "Selected reviewed-note excerpts for your question:"
TIMELINE_PREFIX = "Reviewed visit timeline — exact source sections:"
TIMELINE_SCOPE = (
    "Scope: clinical sections, including observations, medications, labs, "
    "assessment, plans, follow-ups, screening gaps, exams, return precautions "
    "and risk calculations, plus unrecognized headings. Known demographics, "
    "background history, allergy and immunization sections are omitted."
)
SELECTED_SUFFIX = (
    "These are exact excerpts from the selected reviewed-note versions. "
    "Quote matching verifies where the text came from; it does not establish "
    "that the excerpts fully answer the question or describe the current "
    "regimen or outstanding orders. Read later corrections and the surrounding notes."
)
PROVIDER_DISCLAIMER = (
    "Educational support for clinical conversation only. Not a diagnostic device "
    "and not a substitute for professional judgment."
)


class CitationDenied(ValueError):
    """Value-free rejection of a citation contract or retained snapshot."""


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ExcerptSelection(Closed):
    ordinal: StrictInt = Field(ge=1, le=64)
    source_index: StrictInt = Field(ge=0, le=4)
    section_index: StrictInt = Field(ge=0, le=63)


class ReviewedExcerptSelection(Closed):
    schema_version: Literal["1.0.0"]
    enumeration_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["selected", "timeline"]
    excerpts: list[ExcerptSelection] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def ordered(self):
        identities = [(item.source_index, item.section_index) for item in self.excerpts]
        if (
            [item.ordinal for item in self.excerpts]
            != list(range(1, len(identities) + 1))
            or len(set(identities)) != len(identities)
            or (self.mode == "selected" and len(identities) > 8)
        ):
            raise CitationDenied
        return self


class BoundExcerpt(ExcerptSelection):
    note_ref: StrictStr = Field(pattern=REF_PATTERN)
    source_version: StrictInt = Field(ge=1)
    source_at: datetime
    reviewed_at: datetime
    content_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    section: StrictStr = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def aware(self):
        if self.source_at.tzinfo is None or self.reviewed_at.tzinfo is None:
            raise CitationDenied
        self.source_at = self.source_at.astimezone(UTC)
        self.reviewed_at = self.reviewed_at.astimezone(UTC)
        return self


class ReviewedSourceWitness(Closed):
    note_ref: StrictStr = Field(pattern=REF_PATTERN)
    source_version: StrictInt = Field(ge=1)
    source_at: datetime
    reviewed_at: datetime
    content_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    review_ref: StrictStr = Field(pattern=REF_PATTERN)

    @model_validator(mode="after")
    def aware(self):
        if self.source_at.tzinfo is None or self.reviewed_at.tzinfo is None:
            raise CitationDenied
        self.source_at = self.source_at.astimezone(UTC)
        self.reviewed_at = self.reviewed_at.astimezone(UTC)
        return self


class ReviewedProvenance(Closed):
    source_class: Literal["clinician_finalized_note"]
    included: StrictBool
    source_ref: StrictStr = Field(pattern=REF_PATTERN)
    source_version: StrictInt = Field(ge=1)
    source_at: datetime
    author_class: Literal["clinician"]
    reason_code: Literal["eligible_finalized_note"]


class ResultScalars(Closed):
    schema_version: Literal["1.1.0", "1.2.0"]
    state: Literal["ready"]
    reason_code: None
    source_version_digest: StrictStr = Field(pattern=r"^sha256_[a-f0-9]{64}$")
    provenance: list[ReviewedProvenance] = Field(min_length=1, max_length=5)
    completeness: Literal["complete", "partial"]
    current_through: datetime
    generated_at: datetime | None
    brief_state: Literal["ready", "pending", "unavailable"] | None = None

    @model_validator(mode="after")
    def aware(self):
        dates = [self.current_through, *(p.source_at for p in self.provenance)]
        if self.generated_at is not None:
            dates.append(self.generated_at)
        if any(date.tzinfo is None for date in dates) or any(
            not p.included for p in self.provenance
        ):
            raise CitationDenied
        return self


class ContextWitness(Closed):
    schema_version: Literal["1.0.0"]
    context_ref: StrictStr = Field(pattern=REF_PATTERN)
    response_ref: StrictStr = Field(min_length=1, max_length=500)
    request_digest: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    result_scalars: ResultScalars
    artifact_ref: StrictStr | None = Field(default=None, pattern=REF_PATTERN)
    sources: list[ReviewedSourceWitness] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def correspondence(self):
        result = self.result_scalars
        actual = [(s.note_ref, s.source_version, s.source_at) for s in self.sources]
        expected = [
            (p.source_ref, p.source_version, p.source_at) for p in result.provenance
        ]
        if (
            len({s.note_ref for s in self.sources}) != len(actual)
            or len(expected) != len(actual)
            or set(actual) != set(expected)
        ):
            raise CitationDenied
        if result.current_through != max(s.source_at for s in self.sources):
            raise CitationDenied
        if (result.generated_at is not None) != (self.artifact_ref is not None):
            raise CitationDenied
        if result.schema_version == "1.1.0":
            if result.brief_state is not None or self.artifact_ref is None:
                raise CitationDenied
            marker = "reviewed_sources|ready:" + self.artifact_ref
        else:
            if result.brief_state is None or (result.brief_state == "ready") != (
                self.artifact_ref is not None
            ):
                raise CitationDenied
            phase = (
                "ready:" + self.artifact_ref
                if self.artifact_ref
                else "brief_" + result.brief_state
            )
            marker = (
                "reviewed_sources_only|"
                + phase
                + ":"
                + result.source_version_digest.removeprefix("sha256_")
            )
        if self.response_ref != marker:
            raise CitationDenied
        return self


class HistoryMember(Closed):
    turn_ref: StrictStr = Field(pattern=REF_PATTERN)
    user_digest: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    final_digest: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")


class HistoryWitness(Closed):
    context_ref: StrictStr = Field(pattern=REF_PATTERN)
    response_ref: StrictStr = Field(min_length=1, max_length=500)
    request_digest: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    cutoff: datetime
    dependency_ref: StrictStr | None = Field(pattern=REF_PATTERN)
    source_version_digest: StrictStr | None = Field(pattern=r"^sha256_[a-f0-9]{64}$")
    members: list[HistoryMember] = Field(max_length=6)

    @model_validator(mode="after")
    def marker(self):
        if self.cutoff.tzinfo is None or len({m.turn_ref for m in self.members}) != len(
            self.members
        ):
            raise CitationDenied
        self.cutoff = self.cutoff.astimezone(UTC)
        if (
            self.response_ref
            != f"h1|{self.cutoff.isoformat()}|{self.dependency_ref or '-'}"
        ):
            raise CitationDenied
        if (self.dependency_ref is None) != (self.source_version_digest is None):
            raise CitationDenied
        return self


class CitationBindings(Closed):
    schema_version: Literal["1.0.0"]
    selection: ReviewedExcerptSelection
    context_ref: StrictStr = Field(pattern=REF_PATTERN)
    source_version_digest: StrictStr = Field(pattern=r"^sha256_[a-f0-9]{64}$")
    excerpts: list[BoundExcerpt] = Field(min_length=1, max_length=64)
    context_witness: ContextWitness
    history_witness: HistoryWitness | None = None

    @model_validator(mode="after")
    def correspondence(self):
        if (
            self.context_ref != self.context_witness.context_ref
            or self.source_version_digest
            != self.context_witness.result_scalars.source_version_digest
        ):
            raise CitationDenied
        if [item.model_dump() for item in self.selection.excerpts] != [
            ExcerptSelection(
                **{name: getattr(item, name) for name in ExcerptSelection.model_fields}
            ).model_dump()
            for item in self.excerpts
        ]:
            raise CitationDenied
        for item in self.excerpts:
            if item.source_index >= len(self.context_witness.sources):
                raise CitationDenied
            source = self.context_witness.sources[item.source_index]
            if any(
                getattr(item, key) != getattr(source, key)
                for key in (
                    "note_ref",
                    "source_version",
                    "source_at",
                    "reviewed_at",
                    "content_sha256",
                )
            ):
                raise CitationDenied
        return self


class DisplayCitation(Closed):
    citation_ref: StrictStr = Field(pattern=REF_PATTERN)
    ordinal: StrictInt = Field(ge=1, le=64)
    source_at: datetime
    reviewed_at: datetime
    section: StrictStr = Field(min_length=1, max_length=200)


def canonical_selection(value: object) -> dict[str, Any]:
    return ReviewedExcerptSelection.model_validate(value).model_dump(mode="json")


def canonical_bindings(value: object) -> dict[str, Any]:
    try:
        result = CitationBindings.model_validate(value).model_dump(mode="json")
    except ValueError:
        raise CitationDenied from None
    if (
        len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())
        > MAX_BINDING_BYTES
    ):
        raise CitationDenied
    return result


def literal_markdown(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#!|~])", r"\\\1", text)


def content_digest(sections: dict[str, str]) -> str:
    # Same ordered representation as session_prep_note_sources.note_content_sha256.
    if (
        not 1 <= len(sections) <= 64
        or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or "\x00" in key
            or "\x00" in value
            for key, value in sections.items()
        )
        or not any(value.strip() for value in sections.values())
    ):
        raise CitationDenied
    raw = json.dumps(list(sections.items()), separators=(",", ":"), ensure_ascii=False)
    if len(raw) > 18_000:
        raise CitationDenied
    return hashlib.sha256(raw.encode()).hexdigest()


def render_quotes(
    selected: list[tuple[str, datetime, str]],
    *,
    mode: str,
    source_count: int,
    current_through: datetime,
) -> str:
    if current_through.tzinfo is None or not 1 <= source_count <= 5:
        raise CitationDenied
    parts = [TIMELINE_PREFIX if mode == "timeline" else SELECTED_PREFIX]
    if mode == "timeline":
        parts.append(TIMELINE_SCOPE)
    for ordinal, (quote, source_at, section) in enumerate(selected, start=1):
        label = f"{source_at.date().isoformat()} — {' '.join(section.splitlines())}"
        parts.append(
            f"[{ordinal}] {literal_markdown(label)}\n\n"
            + "\n".join("> " + literal_markdown(line) for line in quote.split("\n"))
        )
    parts.append(
        (
            f"Clinical-section excerpts from {source_count} selected reviewed-note "
            "versions are shown in source-date order. Known demographic and disclosed "
            "background-history headings are omitted; unrecognized headings are "
            "retained. Omitted sections are not evidence of absent facts. Dates identify the notes, not "
            "proof that medication was taken or testing was completed. Keep recorded "
            "instructions, corrections and documentation gaps distinct. This is not "
            "the entire chart or a confirmed current regimen or order ledger."
        )
        if mode == "timeline"
        else SELECTED_SUFFIX
    )
    answer = "\n\n".join(parts)
    if len(answer) > 18_000 or (
        mode == "timeline" and len(answer.encode()) > 64 * 1024
    ):
        raise CitationDenied
    notice = (
        f"Source: {source_count} selected reviewed prior "
        f"{'note' if source_count == 1 else 'notes'} through "
        f"{current_through.date().isoformat()}."
    )
    return answer + "\n\n" + notice + "\n\n— " + PROVIDER_DISCLAIMER


def enumeration_digest(sources: list[Any]) -> str:
    """Bind the API's source/section enumeration; never sort JSON object keys."""
    value = [
        [
            source.source_ref,
            source.source_version,
            source.source_at.isoformat(),
            list(source.sections.items()),
        ]
        for source in sources
    ]
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def resolve_bindings(
    *,
    payload: dict[str, Any],
    result: Any,
    review_times: dict[tuple[str, int], datetime] | None = None,
    context_witness: dict[str, Any] | None = None,
    history_witness: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    value = payload.get("reviewed_excerpts")
    if value is None:
        return None
    if (
        payload["outcome"] != "completed"
        or payload["presentation"]["mode"] != "standard"
        or "citations" in payload["presentation"]["suppress"]
        or result is None
        or result.state != "ready"
        or not hasattr(result, "reviewed_sources")
    ):
        raise CitationDenied
    selection = ReviewedExcerptSelection.model_validate(value)
    sources = result.reviewed_sources
    if not 1 <= len(sources) <= 5 or len(
        {source.source_ref for source in sources}
    ) != len(sources):
        raise CitationDenied
    if (
        selection.enumeration_sha256 != enumeration_digest(sources)
        or review_times is None
        or context_witness is None
        or (payload.get("history_context_ref") is not None)
        != (history_witness is not None)
        or (
            history_witness is not None
            and history_witness["context_ref"] != payload["history_context_ref"]
        )
    ):
        raise CitationDenied
    if selection.mode == "timeline":
        expected = [
            (index, section_index)
            for index, source in sorted(
                enumerate(sources), key=lambda pair: pair[1].source_at
            )
            for section_index, (title, text) in enumerate(source.sections.items())
            if text.strip() and " ".join(title.casefold().split()) not in BACKGROUND
        ]
        if [
            (item.source_index, item.section_index) for item in selection.excerpts
        ] != expected or {index for index, _ in expected} != set(range(len(sources))):
            raise CitationDenied
    entries, quotes = [], []
    for item in selection.excerpts:
        if item.source_index >= len(sources):
            raise CitationDenied
        source = sources[item.source_index]
        sections = list(source.sections.items())
        if item.section_index >= len(sections):
            raise CitationDenied
        title, quote = sections[item.section_index]
        if not quote.strip() or len(quote) > 3000:
            raise CitationDenied
        quotes.append((quote, source.source_at, title))
        entries.append(
            BoundExcerpt(
                **item.model_dump(),
                note_ref=source.source_ref,
                source_version=source.source_version,
                source_at=source.source_at,
                reviewed_at=review_times[(source.source_ref, source.source_version)],
                content_sha256=content_digest(source.sections),
                section=title,
            )
        )
    if sum(len(quote) for quote, _, _ in quotes) > 8000 or payload["response"][
        "content"
    ] != render_quotes(
        quotes,
        mode=selection.mode,
        source_count=len(sources),
        current_through=result.current_through,
    ):
        raise CitationDenied
    return canonical_bindings(
        CitationBindings(
            schema_version="1.0.0",
            selection=selection,
            context_ref=payload["provider_context_ref"],
            source_version_digest=result.source_version_digest,
            excerpts=entries,
            context_witness=ContextWitness.model_validate(context_witness),
            history_witness=HistoryWitness.model_validate(history_witness)
            if history_witness is not None
            else None,
        ).model_dump(mode="json")
    )


def resolve_final_bindings(
    *,
    session: Any,
    payload: dict[str, Any],
    result: Any,
    authorized: Any,
    history_witness: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if "reviewed_excerpts" not in payload:
        return None
    if (
        result is None
        or result.state != "ready"
        or not hasattr(result, "reviewed_sources")
    ):
        raise CitationDenied
    from app.services import provider_question_context as prep

    from app.models.authority import AuthorityIdempotencyRecord
    from app.models.authority_conversation import CanonicalConversationTurn as Turn
    from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
    from sqlmodel import select

    receipt = session.get(AuthorityIdempotencyRecord, payload["provider_context_ref"])
    if receipt is None or receipt.request_digest != prep._binding_digest(
        receipt.logical_ref, result
    ):
        raise CitationDenied
    scope = prep._turn_and_scope(session, authorized, receipt.logical_ref)
    turn = session.get(Turn, receipt.logical_ref)
    times, identities = {}, []
    for source in result.reviewed_sources:
        evidence = session.exec(
            select(SessionPrepNoteFinalization).where(
                SessionPrepNoteFinalization.note_id == source.source_ref,
                SessionPrepNoteFinalization.source_version == source.source_version,
            )
        ).one_or_none()
        if evidence is None or evidence.content_sha256 != content_digest(
            source.sections
        ):
            raise CitationDenied
        times[(source.source_ref, source.source_version)] = evidence.finalized_at
        identities.append(
            ReviewedSourceWitness(
                note_ref=source.source_ref,
                source_version=source.source_version,
                source_at=source.source_at,
                reviewed_at=evidence.finalized_at,
                content_sha256=evidence.content_sha256,
                review_ref=str(evidence.id),
            )
        )
    marker = receipt.response_ref
    if not isinstance(marker, str):
        raise CitationDenied
    parts = marker.split("|")[-1].split(":")
    artifact_ref = parts[1] if parts[0] == "ready" and len(parts) in {2, 3} else None
    witness = ContextWitness(
        schema_version="1.0.0",
        context_ref=str(receipt.id),
        response_ref=marker,
        request_digest=receipt.request_digest,
        artifact_ref=artifact_ref,
        result_scalars=ResultScalars.model_validate(
            result.model_dump(
                mode="json",
                exclude={"reviewed_sources", "prepared_brief"},
            )
        ),
        sources=identities,
    )
    bindings = resolve_bindings(
        payload=payload,
        result=result,
        review_times=times,
        context_witness=witness.model_dump(mode="json"),
        history_witness=history_witness,
    )
    retained_context_result(
        session=session,
        scope=scope,
        turn=turn,
        final=type("AdmissionTime", (), {"recorded_at": datetime.now(UTC)})(),
        bindings=CitationBindings.model_validate(bindings),
        now=datetime.now(UTC),
    )
    return bindings


def display_citations(final_ref: str, bindings: object) -> list[dict[str, Any]]:
    if bindings is None:
        return []
    result = CitationBindings.model_validate(canonical_bindings(bindings))
    return [
        DisplayCitation(
            citation_ref=f"{final_ref}.{item.ordinal}",
            ordinal=item.ordinal,
            source_at=item.source_at,
            reviewed_at=item.reviewed_at,
            section=item.section,
        ).model_dump(mode="json")
        for item in result.excerpts
    ]


def retained_context_result(
    *,
    session: Any,
    scope: Any,
    turn: Any,
    final: Any,
    bindings: CitationBindings,
    now: datetime,
) -> Any:
    """Reconstruct the original direct context using only retained reviewed rows.

    Admission calls this under existing source locks; the display consumer must
    also prove its current own-record and canonical user/final/history receipts.
    This helper neither selects current notes nor grants record access.
    """
    from app.services import provider_question_context as prep
    from app.services.session_prep_note_sources import (
        _owned_appointment,
        _prior_patient_scope,
        _utc,
    )
    from app.services.session_prep_runtime import artifact_result

    from app.models.ai_visit_note import AIVisitNote
    from app.models.appointment import Appointment
    from app.models.authority import AuthorityIdempotencyRecord
    from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
    from app.models.session_prep_runtime import SessionPrepArtifact
    from sqlmodel import select

    witness = bindings.context_witness
    target = _owned_appointment(
        session, doctor_id=scope.doctor_id, appointment_id=scope.appointment_id
    )
    if target.patient_id != scope.patient_id:
        raise CitationDenied
    patient_scope = _prior_patient_scope(session, scope.doctor_id, target)
    receipt = session.get(AuthorityIdempotencyRecord, witness.context_ref)
    if (
        turn is None
        or receipt is None
        or (
            receipt.operation != prep.OPERATION
            or receipt.status != "complete"
            or receipt.workload_client_id != turn.workload_client_id
            or receipt.interaction_id != turn.interaction_id
            or receipt.logical_ref != str(turn.id)
            or receipt.response_ref != witness.response_ref
            or receipt.request_digest != witness.request_digest
            or _utc(receipt.retain_until) <= _utc(now)
        )
    ):
        raise CitationDenied
    sources, manifest = [], []
    for item in witness.sources:
        evidence = session.get(SessionPrepNoteFinalization, item.review_ref)
        note = session.get(AIVisitNote, item.note_ref)
        appointment = (
            _owned_appointment(
                session, doctor_id=scope.doctor_id, appointment_id=note.appointment_id
            )
            if note
            else None
        )
        if appointment is None:
            raise CitationDenied
        sections = retained_excerpt(
            binding=item,
            evidence=evidence,
            note=note,
            appointment=appointment,
            doctor_id=scope.doctor_id,
            accepted_at=final.recorded_at,
        )
        if (
            session.exec(
                select(Appointment).where(
                    Appointment.id == appointment.id, patient_scope
                )
            ).one_or_none()
            is None
        ):
            raise CitationDenied
        sources.append(
            dict(
                source_ref=item.note_ref,
                source_version=item.source_version,
                source_at=item.source_at.isoformat(),
                sections=sections,
            )
        )
        manifest.append(
            dict(
                source_ref=item.note_ref,
                source_version=item.source_version,
                source_at=item.source_at.isoformat(),
                content_sha256=item.content_sha256,
                clinician_action_ref=item.review_ref,
            )
        )
    metadata = witness.result_scalars.model_dump(mode="json")
    if metadata["schema_version"] == "1.1.0":
        metadata.pop("brief_state")
    metadata["prepared_brief"] = None
    if witness.artifact_ref is not None:
        artifact = session.get(SessionPrepArtifact, witness.artifact_ref)
        if (
            artifact is None
            or artifact.record_scope_id != scope.id
            or (
                artifact.source_manifest != manifest
                or "sha256_" + artifact.source_digest != bindings.source_version_digest
            )
        ):
            raise CitationDenied
        stored = artifact_result(artifact)
        metadata["prepared_brief"] = (
            stored.prepared_brief.model_dump(mode="json")
            if stored.prepared_brief is not None
            else None
        )
        if stored.brief_generated_at is None or _utc(
            stored.brief_generated_at
        ).isoformat() != metadata["generated_at"].replace("Z", "+00:00"):
            raise CitationDenied
    metadata["reviewed_sources"] = sources
    model = (
        prep.ProviderQuestionSourcesResult
        if metadata["schema_version"] == "1.1.0"
        else prep.ProviderQuestionSourcesOnlyResult
    )
    result = model.model_validate(metadata)
    if prep._binding_digest(str(turn.id), result) != witness.request_digest or (
        enumeration_digest(result.reviewed_sources)
        != bindings.selection.enumeration_sha256
        or result.source_version_digest != bindings.source_version_digest
    ):
        raise CitationDenied
    for item in bindings.excerpts:
        source = witness.sources[item.source_index]
        sections = sources[item.source_index]["sections"]
        if any(
            getattr(item, key) != getattr(source, key)
            for key in (
                "note_ref",
                "source_version",
                "source_at",
                "reviewed_at",
                "content_sha256",
            )
        ) or (
            item.section_index >= len(sections)
            or list(sections)[item.section_index] != item.section
        ):
            raise CitationDenied
    return result


def retained_excerpt(
    *,
    binding: BoundExcerpt | ReviewedSourceWitness,
    evidence: Any,
    note: Any,
    appointment: Any,
    doctor_id: Any,
    accepted_at: datetime,
) -> dict[str, str]:
    from app.services.session_prep_note_sources import _snapshot_content, _utc

    if (
        evidence is None
        or note is None
        or appointment is None
        or str(evidence.note_id) != binding.note_ref
        or str(note.id) != binding.note_ref
        or evidence.source_version != binding.source_version
        or evidence.content_sha256 != binding.content_sha256
        or evidence.doctor_id != doctor_id
        or note.doctor_id != doctor_id
        or appointment.doctor_id != doctor_id
        or evidence.appointment_id != appointment.id
        or note.appointment_id != appointment.id
        or evidence.patient_id != appointment.patient_id
        or _utc(evidence.finalized_at) > _utc(accepted_at)
        or _utc(evidence.finalized_at) != binding.reviewed_at
        or _utc(appointment.appointment_date) != binding.source_at
    ):
        raise CitationDenied
    sections = _snapshot_content(evidence.reviewed_content)
    if content_digest(sections) != binding.content_sha256 or (
        isinstance(binding, BoundExcerpt)
        and (
            binding.section_index >= len(sections)
            or list(sections)[binding.section_index] != binding.section
        )
    ):
        raise CitationDenied
    return sections
