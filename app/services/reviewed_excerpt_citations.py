"""Closed, immutable citation identities; selection never supplies a URL or note ID."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

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


class CitationBindings(Closed):
    schema_version: Literal["1.0.0"]
    selection: ReviewedExcerptSelection
    context_ref: StrictStr = Field(pattern=REF_PATTERN)
    source_version_digest: StrictStr = Field(pattern=r"^sha256_[a-f0-9]{64}$")
    excerpts: list[BoundExcerpt] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def correspondence(self):
        if [item.model_dump() for item in self.selection.excerpts] != [
            ExcerptSelection(
                **{name: getattr(item, name) for name in ExcerptSelection.model_fields}
            ).model_dump()
            for item in self.excerpts
        ]:
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
    selected: list[tuple[str, datetime, str]], *, mode: str, source_count: int
) -> str:
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
    return answer + "\n\n— " + PROVIDER_DISCLAIMER


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
    ] != render_quotes(quotes, mode=selection.mode, source_count=len(sources)):
        raise CitationDenied
    return canonical_bindings(
        CitationBindings(
            schema_version="1.0.0",
            selection=selection,
            context_ref=payload["provider_context_ref"],
            source_version_digest=result.source_version_digest,
            excerpts=entries,
        ).model_dump(mode="json")
    )


def resolve_final_bindings(
    *, session: Any, payload: dict[str, Any], result: Any
) -> dict[str, Any] | None:
    if "reviewed_excerpts" not in payload:
        return None
    if (
        result is None
        or result.state != "ready"
        or not hasattr(result, "reviewed_sources")
    ):
        raise CitationDenied
    from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
    from sqlmodel import select

    times = {}
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
    return resolve_bindings(payload=payload, result=result, review_times=times)


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


def retained_excerpt(
    *,
    binding: BoundExcerpt,
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
    if (
        content_digest(sections) != binding.content_sha256
        or binding.section_index >= len(sections)
        or list(sections)[binding.section_index] != binding.section
    ):
        raise CitationDenied
    return sections


def read_retained_citation(
    *, session: Any, doctor: Any, final_ref: Any, ordinal: int
) -> dict[str, Any]:
    from app.services import authority_conversation as conversation
    from app.services import authority_conversation_read as history
    from app.services.provider_record_authority import validate_provider_record_scope
    from app.services.session_prep_note_sources import _owned_appointment

    from app.models.ai_visit_note import AIVisitNote
    from app.models.authority import (
        AuthorityPersonBinding,
        InteractionLineage,
        MountContextSession,
    )
    from app.models.authority_conversation import (
        CanonicalConversationFinalPhase as Final,
    )
    from app.models.authority_conversation import CanonicalConversationTurn as Turn
    from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
    from sqlmodel import select

    final = session.get(Final, final_ref)
    turn = session.get(Turn, final.turn_id) if final else None
    lineage = session.get(InteractionLineage, turn.interaction_id) if turn else None
    mount = session.get(MountContextSession, lineage.session_id) if lineage else None
    binding = (
        session.get(AuthorityPersonBinding, mount.person_binding_id) if mount else None
    )
    if binding is None or turn is None or final is None or mount is None:
        raise CitationDenied
    scope = validate_provider_record_scope(
        session=session,
        binding=binding,
        actor_id=turn.actor_id,
        clerk_subject=doctor.clerk_id,
    )
    if scope is None or scope.doctor_id != doctor.id:
        raise CitationDenied
    row = session.exec(
        history._query(turn.workload_client_id, mount, scope).where(
            Final.id == final.id
        )
    ).one_or_none()
    if (
        row is None
        or final.outcome != "completed"
        or final.presentation_mode != "standard"
        or final.suppress_citations
    ):
        raise CitationDenied
    bindings = CitationBindings.model_validate(
        canonical_bindings(final.reviewed_excerpt_bindings)
    )
    payload: dict[str, Any] = {
        "phase": "final_response",
        "outcome": final.outcome,
        "response": {"content_type": final.content_type, "content": final.content},
        "presentation": history._presentation(final),
        "reviewed_excerpts": bindings.selection.model_dump(mode="json"),
    }
    for operation, key in (
        (history.prep.OPERATION, "provider_context_ref"),
        (history.OPERATION, "history_context_ref"),
    ):
        receipt = history._receipt(session, turn, operation)
        if receipt is not None:
            payload[key] = str(receipt.id)
    if (
        payload.get("provider_context_ref") != bindings.context_ref
        or final.request_digest
        != conversation._request_digest(
            conversation._canonical_payload(payload), final.reviewed_excerpt_bindings
        )
        or not 1 <= ordinal <= len(bindings.excerpts)
    ):
        raise CitationDenied
    item = bindings.excerpts[ordinal - 1]
    evidence = session.exec(
        select(SessionPrepNoteFinalization).where(
            SessionPrepNoteFinalization.note_id == item.note_ref,
            SessionPrepNoteFinalization.source_version == item.source_version,
        )
    ).one_or_none()
    note = session.get(AIVisitNote, item.note_ref)
    appointment = (
        _owned_appointment(
            session, doctor_id=doctor.id, appointment_id=note.appointment_id
        )
        if note
        else None
    )
    sections = retained_excerpt(
        binding=item,
        evidence=evidence,
        note=note,
        appointment=appointment,
        doctor_id=doctor.id,
        accepted_at=final.recorded_at,
    )
    return {
        "schema_version": "1.0.0",
        "citation": display_citations(str(final.id), bindings.model_dump(mode="json"))[
            ordinal - 1
        ],
        "content_sha256": item.content_sha256,
        "sections": [
            {"title": title, "text": text} for title, text in sections.items()
        ],
    }
