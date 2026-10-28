"""Owner-scoped display preference. It does not prepare or rewrite any note."""

from typing import Any, cast

from pydantic import BaseModel, Field

from activemodel.types import TypeIDType
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_template_preference import NoteTemplatePreference
from sqlmodel import Session, select


class TemplatePreference(BaseModel, extra="forbid"):
    default_schema_ref: str | None
    revision: int = Field(ge=0)


def preference_row(
    session: Session, doctor_id: TypeIDType
) -> NoteTemplatePreference | None:
    return session.exec(
        select(NoteTemplatePreference).where(
            NoteTemplatePreference.doctor_id == doctor_id
        )
    ).one_or_none()


def preference_snapshot(session: Session, doctor_id: TypeIDType) -> TemplatePreference:
    row = preference_row(session, doctor_id)
    selected = None
    if row is not None and row.default_schema_id is not None:
        # A deleted or reassigned template cannot remain an effective default.
        selected = session.exec(
            select(DoctorNoteSchema).where(
                DoctorNoteSchema.id == row.default_schema_id,
                DoctorNoteSchema.doctor_id == doctor_id,
                cast(Any, DoctorNoteSchema.deleted_at).is_(None),
            )
        ).one_or_none()
    return TemplatePreference(
        default_schema_ref=str(selected.id) if selected is not None else None,
        revision=row.revision if row is not None else 0,
    )
