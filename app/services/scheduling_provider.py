"""SchedulingProvider interface, normalized event shape, and match resolution.

Implements contracts/scheduling-provider-v1.md (cosmaneura-architecture,
ADR 0027). Adapters (scheduling_acuity.py, scheduling_google_calendar.py) are
dumb: they only fetch and normalize. This module is where identity
decisions happen, and it is provider-agnostic.
"""

from datetime import datetime, timezone
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from app import log
from app.services.external_webhook_inbox import provider_log_ref

from activemodel.session_manager import get_session
from app.models.ambiguous_patient_match import (
    AmbiguousPatientMatch,
    AmbiguousPatientMatchStatus,
)
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.patient_external_identity import (
    PatientExternalIdentity,
    SchedulingProviderSource,
)
from sqlmodel import select


class NormalizedAppointmentEvent(BaseModel):
    """One appointment/event as reported by a SchedulingProvider adapter.

    See contracts/scheduling-provider-v1.md for the field rules; this model
    is that contract's Python shape.
    """

    source: SchedulingProviderSource
    external_appointment_ref: str
    external_patient_ref: str | None = None
    doctor_external_calendar_ref: str
    start_at: datetime
    end_at: datetime
    patient_name: str | None = None
    patient_email: str | None = None
    patient_phone: str | None = None
    patient_date_of_birth: datetime | None = None
    appointment_label: str | None = None
    created_by: Literal["patient_self_scheduled", "staff_scheduled"] | None = None
    canceled: bool = False


@runtime_checkable
class SchedulingProvider(Protocol):
    name: str

    def fetch_upcoming(
        self, doctor: Doctor, since: datetime
    ) -> list[NormalizedAppointmentEvent]:
        """Events from `since` onward for one doctor's connected calendar."""
        ...

    def fetch_historical(
        self, doctor: Doctor, before: datetime, cursor: str | None
    ) -> tuple[list[NormalizedAppointmentEvent], str | None]:
        """One page of events strictly before `before`.

        Returns (events, next_cursor). next_cursor is None when the source
        has no more historical pages.
        """
        ...


class ResolutionOutcome(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    appointment: Appointment | None = None
    queued: AmbiguousPatientMatch | None = None


def _doctor_ref(doctor_id) -> str:
    return provider_log_ref(f"doctor:{doctor_id}")


def resolve_event(
    doctor: Doctor, event: NormalizedAppointmentEvent
) -> ResolutionOutcome:
    """Resolve one normalized event to a canonical Appointment, per the
    Matching section of contracts/scheduling-provider-v1.md:

    1. Appointment exact match on (doctor_id, external_id) -> update.
    2. Patient exact match via PatientExternalIdentity -> create Appointment
       against that Patient.
    3. No confident match -> queue for doctor confirmation. Never guessed.
    """
    with get_session() as session:
        existing_appointment = session.exec(
            select(Appointment).where(
                Appointment.doctor_id == doctor.id,
                Appointment.external_id == event.external_appointment_ref,
            )
        ).one_or_none()

        if existing_appointment is not None:
            existing_appointment.appointment_date = event.start_at
            if event.appointment_label:
                existing_appointment.appointment_name = event.appointment_label
            session.add(existing_appointment)
            session.commit()
            session.refresh(existing_appointment)
            log.info(
                "scheduling event resolved",
                event_code="scheduling_event_matched_appointment",
                doctor_ref=_doctor_ref(doctor.id),
                source=event.source.value,
                status="updated",
            )
            return ResolutionOutcome(appointment=existing_appointment)

        matched_patient: Patient | None = None
        if event.external_patient_ref:
            identity = session.exec(
                select(PatientExternalIdentity).where(
                    PatientExternalIdentity.doctor_id == doctor.id,
                    PatientExternalIdentity.source == event.source,
                    PatientExternalIdentity.external_patient_ref
                    == event.external_patient_ref,
                )
            ).one_or_none()
            if identity is not None:
                matched_patient = session.get(Patient, identity.patient_id)

        if matched_patient is not None:
            new_appointment = Appointment(
                doctor_id=doctor.id,
                patient_id=matched_patient.id,
                appointment_date=event.start_at,
                external_id=event.external_appointment_ref,
                appointment_name=event.appointment_label,
                created_by_system=event.source.value,
            )
            session.add(new_appointment)
            session.commit()
            session.refresh(new_appointment)
            log.info(
                "scheduling event resolved",
                event_code="scheduling_event_matched_patient",
                doctor_ref=_doctor_ref(doctor.id),
                source=event.source.value,
                status="created",
            )
            return ResolutionOutcome(appointment=new_appointment)

        # No confident match. Never fuzzy-match by name/date of birth here —
        # queue for the doctor to confirm instead.
        queued = AmbiguousPatientMatch(
            doctor_id=doctor.id,
            source=event.source,
            external_appointment_ref=event.external_appointment_ref,
            external_patient_ref=event.external_patient_ref,
            candidate_patient_name=event.patient_name,
            candidate_patient_email=event.patient_email,
            candidate_patient_phone=event.patient_phone,
            candidate_appointment_start_at=event.start_at,
        )
        session.add(queued)
        session.commit()
        session.refresh(queued)
        log.info(
            "scheduling event queued for confirmation",
            event_code="scheduling_event_queued",
            doctor_ref=_doctor_ref(doctor.id),
            source=event.source.value,
        )
        return ResolutionOutcome(queued=queued)


def confirm_queued_match(
    queued: AmbiguousPatientMatch, *, patient: Patient, resolved_by: str
) -> Appointment:
    """A doctor confirms which Patient a queued scheduling-sync row belongs to.

    Creates the Appointment, backfills PatientExternalIdentity so the same
    source-side identity auto-resolves on every later sync, and marks the
    queue row resolved.
    """
    with get_session() as session:
        appointment = Appointment(
            doctor_id=queued.doctor_id,
            patient_id=patient.id,
            appointment_date=queued.candidate_appointment_start_at,
            external_id=queued.external_appointment_ref,
            created_by_system=queued.source.value,
        )
        session.add(appointment)

        if queued.external_patient_ref:
            session.add(
                PatientExternalIdentity(
                    doctor_id=queued.doctor_id,
                    patient_id=patient.id,
                    source=queued.source,
                    external_patient_ref=queued.external_patient_ref,
                )
            )

        session.commit()
        session.refresh(appointment)

        db_queued = session.get(AmbiguousPatientMatch, queued.id)
        assert db_queued is not None
        db_queued.status = AmbiguousPatientMatchStatus.resolved
        db_queued.resolved_patient_id = patient.id
        db_queued.resolved_appointment_id = appointment.id
        db_queued.resolved_at = datetime.now(timezone.utc)
        db_queued.resolved_by = resolved_by
        session.add(db_queued)
        session.commit()

    log.info(
        "scheduling queue entry confirmed",
        event_code="scheduling_queue_confirmed",
        doctor_ref=_doctor_ref(queued.doctor_id),
    )
    return appointment
