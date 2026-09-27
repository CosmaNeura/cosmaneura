"""Current saved Core sources for an already authorized encounter; no capture."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.services import patient_context
from app.services.scribe_capture_scope import CaptureScopeDenied

from activemodel.session_manager import get_session
from app.models.appointment import Appointment
from app.models.intake.intake import Intake
from sqlmodel import Session, select


class PrepSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    source_ref: str = Field(
        pattern=r"^(?:visit_note|intake)_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    appointment_ref: str = Field(pattern=r"^appointment_[0-7][0-9a-hjkmnp-tv-z]{25}$")
    kind: Literal["generated_visit_note", "patient_intake_summary"]
    label: str = Field(min_length=1, max_length=200)
    source_at: AwareDatetime
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(max_length=8000, repr=False)
    truncated: bool


class PrepReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    appointment_ref: str
    sources: tuple[PrepSource, ...] = Field(max_length=6)
    additional_notes_available: bool
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _source(row, appointment, kind, label, fields):
    content = "\n\n".join(f"{key}\n{value}" for key, value in fields.items())
    return PrepSource(
        source_ref=str(row.id),
        appointment_ref=str(appointment.id),
        kind=kind,
        label=label[:200],
        source_at=appointment.appointment_date.replace(tzinfo=UTC)
        if appointment.appointment_date.tzinfo is None
        else appointment.appointment_date,
        content_sha256=_hash(fields),
        content=content[:8000],
        truncated=len(content) > 8000,
    )


def read_encounter_prep(*, caller, binding_ref: str, workspace_id: UUID) -> PrepReceipt:
    if not caller.capture_settings.prep_enabled:
        raise CaptureScopeDenied
    with get_session() as managed:
        session = cast(Session, managed)
        binding = caller.authorize(session, binding_ref, workspace_id)
        appointment = session.get(Appointment, binding.appointment_id)
        if appointment is None:
            raise CaptureScopeDenied
        sources = []
        intake = session.exec(
            select(Intake)
            .where(
                Intake.appointment_id == appointment.id,
                Intake.doctor_id == binding.doctor_id,
                cast(Any, Intake.deleted_at).is_(None),
                cast(Any, Intake.summary).is_not(None),
            )
            .order_by(cast(Any, Intake.updated_at).desc(), Intake.id.desc())
            .limit(1)
        ).first()
        if intake and intake.summary:
            sources.append(
                _source(
                    intake,
                    appointment,
                    "patient_intake_summary",
                    "Patient intake · AI summary",
                    {"Summary": intake.summary},
                )
            )
        recent = patient_context.recent_notes_for_patient(
            binding.doctor_id,
            binding.patient_id,
            limit=6,
            before=min(
                appointment.appointment_date.replace(tzinfo=UTC)
                if appointment.appointment_date.tzinfo is None
                else appointment.appointment_date,
                datetime.now(UTC),
            ),
            exclude_appointment_id=appointment.id,
        )
        for note in recent[:5]:
            prior = session.get(Appointment, note.appointment_id)
            # Recheck malformed/historic row ownership before releasing content.
            if (
                prior is None
                or prior.doctor_id != binding.doctor_id
                or prior.patient_id != binding.patient_id
            ):
                raise CaptureScopeDenied
            sources.append(
                _source(
                    note,
                    prior,
                    "generated_visit_note",
                    f"{note.note_type[:170]} · generated draft",
                    note.note,
                )
            )
        value = {
            "appointment_ref": str(appointment.id),
            "sources": [source.model_dump(mode="json") for source in sources],
            "additional_notes_available": len(recent) > 5,
        }
        result = PrepReceipt(**value, snapshot_sha256=_hash(value))
        session.commit()
        return result
