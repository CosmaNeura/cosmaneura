"""Explicit clinician review and bounded, current-only Session Prep sources."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlalchemy import func
from sqlmodel import Session, select

MAX_SOURCE_CHARACTERS = 18_000
MAX_FINALIZED_NOTES = 5
MAX_REVIEW_CANDIDATES = 128
SOURCE_WINDOW = timedelta(days=548)


class NoteSourceDenied(ValueError):
    """Value-free refusal of inaccessible, stale, or unreviewable material."""


class NoteSourceChanged(NoteSourceDenied):
    """The note differs from the revision shown for clinician review."""


def canonical_note_content(value: object) -> str:
    """Canonicalize only bounded note sections; never stringify arbitrary data."""
    if (
        not isinstance(value, dict)
        or not value
        or len(value) > 64
        or any(
            not isinstance(key, str) or not key.strip() or not isinstance(text, str)
            for key, text in value.items()
        )
        or not any(text.strip() for text in value.values())
    ):
        raise NoteSourceDenied("Note cannot be used for Session Prep")
    content = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    if len(content) > MAX_SOURCE_CHARACTERS:
        raise NoteSourceDenied("Note exceeds the Session Prep source bound")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        raise NoteSourceDenied("Note cannot be used for Session Prep") from None
    return content


def note_content_sha256(value: object) -> str:
    return hashlib.sha256(canonical_note_content(value).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    # Existing appointment timestamps are stored without an offset and use UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _owned_appointment(
    session: Session, *, doctor_id: TypeIDType, appointment_id: TypeIDType
) -> Appointment:
    doctor = session.get(Doctor, doctor_id)
    appointment = session.get(Appointment, appointment_id)
    patient = session.get(Patient, appointment.patient_id) if appointment else None
    if (
        doctor is None
        or doctor.deleted_at is not None
        or appointment is None
        or appointment.doctor_id != doctor.id
        or patient is None
        or patient.doctor_id != doctor.id
    ):
        raise NoteSourceDenied("Note scope is unavailable")
    return appointment


def finalize_note_for_session_prep(
    *,
    session: Session,
    doctor_id: TypeIDType,
    note_id: TypeIDType,
    expected_content_sha256: str,
    observed_at: datetime,
) -> SessionPrepNoteFinalization:
    """Record a verified clinician's explicit review in the caller's transaction.

    HTTP callers must use the authenticated actor, reject view-as, and pass the
    digest of the exact displayed revision. No background job may infer review.
    Locking the note serializes duplicate actions and version allocation.
    """
    if (
        observed_at.tzinfo is None
        or not isinstance(expected_content_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_content_sha256) is None
    ):
        raise NoteSourceDenied("Invalid note review request")
    note = session.exec(
        select(AIVisitNote)
        .where(AIVisitNote.id == note_id)
        .with_for_update(of=AIVisitNote)
    ).one_or_none()
    if note is None or note.doctor_id != doctor_id or note.superseded_at is not None:
        raise NoteSourceDenied("Note scope is unavailable")
    appointment = _owned_appointment(
        session, doctor_id=doctor_id, appointment_id=note.appointment_id
    )
    digest = note_content_sha256(note.note)
    if not hmac.compare_digest(digest, expected_content_sha256):
        raise NoteSourceChanged("The note changed; review the current revision")
    existing = session.exec(
        select(SessionPrepNoteFinalization).where(
            SessionPrepNoteFinalization.note_id == note.id,
            SessionPrepNoteFinalization.content_sha256 == digest,
        )
    ).one_or_none()
    if existing is not None:
        if (
            existing.doctor_id != doctor_id
            or existing.appointment_id != appointment.id
            or existing.patient_id != appointment.patient_id
            or note_content_sha256(existing.reviewed_content) != digest
        ):
            raise NoteSourceDenied("Note review scope is unavailable")
        return existing
    last_version = session.exec(
        select(func.max(SessionPrepNoteFinalization.source_version)).where(
            SessionPrepNoteFinalization.note_id == note.id
        )
    ).one()
    finalized = SessionPrepNoteFinalization(
        note_id=note.id,
        doctor_id=doctor_id,
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        source_version=(last_version or 0) + 1,
        content_sha256=digest,
        reviewed_content=json.loads(canonical_note_content(note.note)),
        finalized_at=observed_at,
    )
    session.add(finalized)
    session.flush()
    return finalized


@dataclass(frozen=True)
class ReviewedNoteSource:
    source_ref: str
    source_version: int
    source_at: datetime
    clinician_action_ref: str
    content_sha256: str
    content: dict[str, str] = field(repr=False)


@dataclass(frozen=True)
class ReviewedNoteSelection:
    sources: tuple[ReviewedNoteSource, ...]
    truncated: bool


def load_reviewed_note_sources(
    *,
    session: Session,
    doctor_id: TypeIDType,
    target_appointment_id: TypeIDType,
    observed_at: datetime,
) -> ReviewedNoteSelection:
    """Select up to five still-current reviewed notes for one owned encounter.

    Authority must be checked by the caller on every use, including replay.
    This adapter independently narrows selection to the doctor's own patient,
    the prior 548 days, intact finalization evidence, and current note content.
    """
    if observed_at.tzinfo is None:
        raise NoteSourceDenied("Invalid source observation time")
    target = _owned_appointment(
        session, doctor_id=doctor_id, appointment_id=target_appointment_id
    )
    cutoff = min(_utc(target.appointment_date), observed_at.astimezone(UTC))
    rows = session.exec(
        select(SessionPrepNoteFinalization, AIVisitNote, Appointment)
        .join(AIVisitNote, AIVisitNote.id == SessionPrepNoteFinalization.note_id)
        .join(Appointment, Appointment.id == AIVisitNote.appointment_id)
        .where(
            SessionPrepNoteFinalization.doctor_id == doctor_id,
            SessionPrepNoteFinalization.patient_id == target.patient_id,
            SessionPrepNoteFinalization.appointment_id == Appointment.id,
            AIVisitNote.doctor_id == doctor_id,
            cast(Any, AIVisitNote.superseded_at).is_(None),
            Appointment.doctor_id == doctor_id,
            Appointment.patient_id == target.patient_id,
            Appointment.id != target.id,
            Appointment.appointment_date >= cutoff - SOURCE_WINDOW,
            Appointment.appointment_date < cutoff,
            SessionPrepNoteFinalization.finalized_at <= observed_at,
        )
        .order_by(
            cast(Any, Appointment.appointment_date).desc(),
            AIVisitNote.id,
            cast(Any, SessionPrepNoteFinalization.source_version).desc(),
        )
        .limit(MAX_REVIEW_CANDIDATES + 1)
    )
    selected: list[ReviewedNoteSource] = []
    seen: set[str] = set()
    for index, (evidence, note, appointment) in enumerate(rows):
        if index == MAX_REVIEW_CANDIDATES:
            return ReviewedNoteSelection(tuple(selected), truncated=True)
        note_ref = str(note.id)
        if note_ref in seen:
            continue
        try:
            current_digest = note_content_sha256(note.note)
            evidence_digest = note_content_sha256(evidence.reviewed_content)
        except NoteSourceDenied:
            continue
        if not hmac.compare_digest(
            current_digest, evidence.content_sha256
        ) or not hmac.compare_digest(evidence_digest, evidence.content_sha256):
            continue
        seen.add(note_ref)
        if len(selected) == MAX_FINALIZED_NOTES:
            return ReviewedNoteSelection(tuple(selected), truncated=True)
        selected.append(
            ReviewedNoteSource(
                source_ref=note_ref,
                source_version=evidence.source_version,
                source_at=_utc(appointment.appointment_date),
                clinician_action_ref=str(evidence.id),
                content_sha256=evidence.content_sha256,
                content=json.loads(canonical_note_content(evidence.reviewed_content)),
            )
        )
    return ReviewedNoteSelection(tuple(selected), truncated=False)
