"""One owned, patient-independent SOAP starting point; deletion stays deleted."""

from dataclasses import dataclass
from typing import Literal

from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_template_preference import NoteTemplatePreference
from sqlalchemy import func
from sqlmodel import Session, col, select

SOAP_FIELDS = {
    "Subjective": "",
    "Objective": "",
    "Assessment": "",
    "Plan": "",
}
MAX_CATALOGUE_TEMPLATES = 100


class DefaultTemplateOwnerUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class DefaultTemplateResult:
    status: Literal["available", "deleted", "capacity"]
    created: bool = False


def provision_default_note_template(
    session: Session, doctor_id: TypeIDType
) -> DefaultTemplateResult:
    """Explicit initialization transaction; never called by catalogue reads.

    The existing unique owner/name constraint and retained soft-deleted SOAP
    row are the durable one-time receipt. Never edit an existing SOAP template,
    its deletion, another template, an existing default preference, or a saved note.
    Callers own authorization and the transaction's commit/rollback.
    """
    owner = session.exec(
        select(Doctor)
        .where(Doctor.id == doctor_id, col(Doctor.deleted_at).is_(None))
        .execution_options(populate_existing=True)
        .with_for_update()
    ).one_or_none()
    if owner is None:
        raise DefaultTemplateOwnerUnavailable("default_template_owner_unavailable")
    prior = session.exec(
        select(DoctorNoteSchema).where(
            DoctorNoteSchema.doctor_id == owner.id,
            DoctorNoteSchema.note_type == "SOAP",
        )
    ).one_or_none()
    if prior is not None:
        return DefaultTemplateResult("deleted" if prior.deleted_at else "available")
    count = session.exec(
        select(func.count())
        .select_from(DoctorNoteSchema)
        .where(
            DoctorNoteSchema.doctor_id == owner.id,
            col(DoctorNoteSchema.deleted_at).is_(None),
        )
    ).one()
    if count >= MAX_CATALOGUE_TEMPLATES:
        # Keep the existing native catalogue bound and every custom template.
        # Explicit initialization reports this exception; capture is not blocked.
        return DefaultTemplateResult("capacity")
    schema = DoctorNoteSchema(
        doctor_id=owner.id, note_type="SOAP", fields=dict(SOAP_FIELDS)
    )
    session.add(schema)
    session.flush()
    preference = session.exec(
        select(NoteTemplatePreference).where(
            NoteTemplatePreference.doctor_id == owner.id
        )
    ).one_or_none()
    if count == 0 and preference is None:
        session.add(
            NoteTemplatePreference(doctor_id=owner.id, default_schema_id=schema.id)
        )
        session.flush()
    return DefaultTemplateResult("available", created=True)
