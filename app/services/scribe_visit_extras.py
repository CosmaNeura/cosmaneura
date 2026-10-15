"""Bounded author reminders and explicit identity decisions under visit locks."""

from datetime import UTC, date, datetime, time

from typeid import TypeID

from app.services import authority
from app.services.companion_encounter_contract import digest
from app.services.scribe_encounter_contract import ConfirmNewPatient, ExistingPatient
from app.utils.encryption import pseudonymous_digest

from app.models.authority import AuthorityAuditEvent
from app.models.patient import Patient
from app.models.scribe_patient_identity import ScribePatientIdentity
from app.models.scribe_private_note import ScribePrivateNote, ScribePrivateNoteReceipt
from sqlalchemy import func
from sqlmodel import col, select


def identity_row(session, row, link):
    identity = session.exec(
        select(ScribePatientIdentity)
        .where(
            ScribePatientIdentity.appointment_id == row.appointment_id,
        )
        .with_for_update()
    ).one_or_none()
    if identity is not None and (
        identity.doctor_id != row.doctor_id
        or identity.actor_id != link.actor_id
        or identity.tenant_id != link.tenant_id
        or identity.provider_link_id != link.id
        or identity.source_patient_id != row.patient_id
    ):
        raise authority.AuthorityRequestDeniedError
    return identity


def identity_view(session, row, link, source_patient=None):
    identity = identity_row(session, row, link)
    patient_id = identity.canonical_patient_id if identity else row.patient_id
    patient = (
        source_patient
        if source_patient is not None and source_patient.id == patient_id
        else session.exec(
            select(Patient)
            .where(
                Patient.id == patient_id,
                Patient.doctor_id == row.doctor_id,
            )
            .with_for_update(read=True, key_share=True)
        ).one_or_none()
    )
    if patient is None or patient.doctor_id != row.doctor_id:
        raise authority.AuthorityRequestDeniedError
    return {
        "state": identity.state if identity else "confirmed",
        "version": identity.version if identity else 0,
        "canonical_patient_ref": str(patient.id),
        "name": patient.name,
        "date_of_birth": patient.date_of_birth.date().isoformat()
        if patient.date_of_birth
        else None,
    }


def private_note(session, row, link):
    note = session.exec(
        select(ScribePrivateNote)
        .where(
            ScribePrivateNote.appointment_id == row.appointment_id,
            ScribePrivateNote.doctor_id == row.doctor_id,
            ScribePrivateNote.actor_id == link.actor_id,
            ScribePrivateNote.tenant_id == link.tenant_id,
            ScribePrivateNote.provider_link_id == link.id,
        )
        .with_for_update()
    ).one_or_none()
    return note


def private_note_view(note):
    return {
        "text": note.text if note else "",
        "version": note.version if note else 0,
        "updated_at": note.saved_at.astimezone(UTC).isoformat() if note else None,
    }


def save_private_note(session, request, row, link, conflict):
    # The keyed digest prevents low-entropy reminder content being guessed
    # from retry evidence. No clinical source or audit field receives text.
    request_digest = pseudonymous_digest(
        request.model_dump_json(exclude={"request_nonce"}),
        context="scribe-private-note-change",
    )
    receipt = session.exec(
        select(ScribePrivateNoteReceipt).where(
            ScribePrivateNoteReceipt.actor_id == link.actor_id,
            ScribePrivateNoteReceipt.tenant_id == link.tenant_id,
            ScribePrivateNoteReceipt.change_key == request.change_key,
        )
    ).one_or_none()
    note = private_note(session, row, link)
    if receipt is not None:
        if (
            receipt.request_digest != request_digest
            or receipt.appointment_id != row.appointment_id
            or receipt.provider_link_id != link.id
            or note is None
        ):
            raise conflict
        return private_note_view(note), False
    if request.expected_version != (note.version if note else 0):
        raise conflict
    now = datetime.now(UTC)
    if note is None:
        note = ScribePrivateNote(
            appointment_id=row.appointment_id,
            doctor_id=row.doctor_id,
            actor_id=link.actor_id,
            tenant_id=link.tenant_id,
            provider_link_id=link.id,
            text=request.text,
            version=1,
            saved_at=now,
        )
    else:
        note.text, note.version, note.saved_at = request.text, note.version + 1, now
    session.add(note)
    session.add(
        ScribePrivateNoteReceipt(
            appointment_id=row.appointment_id,
            actor_id=link.actor_id,
            tenant_id=link.tenant_id,
            provider_link_id=link.id,
            change_key=request.change_key,
            request_digest=request_digest,
            saved_version=note.version,
        )
    )
    session.flush()
    return private_note_view(note), True


def unselectable_source_patients():
    return select(col(ScribePatientIdentity.source_patient_id)).where(
        ScribePatientIdentity.provisional_entry == True,  # noqa: E712
        col(ScribePatientIdentity.state).in_(("provisional", "matched")),
    )


def match_candidates(session, request, row):
    query = select(Patient).where(
        Patient.doctor_id == row.doctor_id, Patient.id != row.patient_id
    )
    query = query.where(col(Patient.id).not_in(unselectable_source_patients()))
    if request.query:
        query = query.where(col(Patient.name).icontains(request.query, autoescape=True))
    if request.date_of_birth:
        query = query.where(func.date(Patient.date_of_birth) == request.date_of_birth)
    patients = list(
        session.exec(query.order_by(col(Patient.name), col(Patient.id)).limit(101))
    )
    return {
        "patients": [
            {
                "patient_ref": str(p.id),
                "name": p.name,
                "date_of_birth": p.date_of_birth.date().isoformat()
                if p.date_of_birth
                else None,
            }
            for p in patients[:100]
        ],
        "more_results": len(patients) > 100,
    }


def save_match(session, request, row, link, conflict):
    identity = identity_row(session, row, link)
    request_digest = digest(request.model_dump(mode="json", exclude={"request_nonce"}))
    receipt_ref = "scribe_patient_match:" + str(request.change_key)
    receipt = session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.actor_id == link.actor_id,
            AuthorityAuditEvent.tenant_id == link.tenant_id,
            AuthorityAuditEvent.event_type == "scribe.patient.identity_confirmed",
            AuthorityAuditEvent.resource_ref == receipt_ref,
        )
    ).one_or_none()
    if receipt is not None:
        if receipt.request_digest != request_digest:
            raise conflict
        return False
    if request.expected_version != (identity.version if identity else 0):
        raise conflict
    if isinstance(request.selection, ExistingPatient):
        patient_id = TypeID.from_string(request.selection.patient_ref)
        unselectable = session.exec(
            unselectable_source_patients()
            .where(ScribePatientIdentity.source_patient_id == patient_id)
            .limit(1)
        ).first()
        if unselectable is not None or patient_id == row.patient_id:
            raise conflict
        state = "matched"
    else:
        patient_id, state = row.patient_id, "confirmed"
    patient = session.exec(
        select(Patient)
        .where(
            Patient.id == patient_id,
            Patient.doctor_id == row.doctor_id,
        )
        .with_for_update()
    ).one_or_none()
    if patient is None:
        raise authority.AuthorityRequestDeniedError
    now = datetime.now(UTC)
    if (
        isinstance(request.selection, ConfirmNewPatient)
        and request.selection.date_of_birth is not None
    ):
        birthday = request.selection.date_of_birth
        if not date(1900, 1, 1) <= birthday <= now.date():
            raise ValueError("birth_date_out_of_range")
        if patient.date_of_birth and patient.date_of_birth.date() != birthday:
            raise conflict
        patient.date_of_birth = datetime.combine(birthday, time.min)
        session.add(patient)
    if identity is None:
        identity = ScribePatientIdentity(
            appointment_id=row.appointment_id,
            doctor_id=row.doctor_id,
            actor_id=link.actor_id,
            tenant_id=link.tenant_id,
            provider_link_id=link.id,
            source_patient_id=row.patient_id,
            canonical_patient_id=patient.id,
            state=state,
            version=1,
        )
    else:
        identity.canonical_patient_id, identity.state = patient_id, state
        identity.version += 1
    session.add(identity)
    session.add(
        AuthorityAuditEvent(
            event_source="platform_authoritative",
            event_type="scribe.patient.identity_confirmed",
            outcome="accepted",
            occurred_at=now,
            recorded_at=now,
            tenant_id=link.tenant_id,
            actor_id=link.actor_id,
            resource_type="scribe_encounter_identity",
            resource_ref=receipt_ref,
            request_digest=request_digest,
        )
    )
    session.flush()
    return True
