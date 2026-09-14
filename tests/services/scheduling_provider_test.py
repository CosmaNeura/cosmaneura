from datetime import datetime, timedelta, timezone

from app.services.scheduling_provider import (
    NormalizedAppointmentEvent,
    confirm_queued_match,
    resolve_event,
)

from app.models.ambiguous_patient_match import (
    AmbiguousPatientMatch,
    AmbiguousPatientMatchStatus,
)
from app.models.appointment import Appointment
from app.models.patient_external_identity import (
    PatientExternalIdentity,
    SchedulingProviderSource,
)

from tests.factories import DoctorFactory, PatientFactory


def _event(**overrides) -> NormalizedAppointmentEvent:
    base = NormalizedAppointmentEvent(
        source=SchedulingProviderSource.acuity,
        external_appointment_ref="acuity-appt-1",
        external_patient_ref=None,
        doctor_external_calendar_ref="calendar-1",
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
        end_at=datetime.now(timezone.utc) + timedelta(days=1, hours=1),
        patient_name="Jane Cosmo",
        patient_email="jane.cosmo@example.com",
        patient_phone="4061234567",
    )
    return base.model_copy(update=overrides)


def test_resolve_event_with_no_external_patient_ref_queues_for_confirmation():
    doctor = DoctorFactory.build().save()
    event = _event(external_patient_ref=None)

    outcome = resolve_event(doctor, event)

    assert outcome.appointment is None
    assert outcome.queued is not None
    assert outcome.queued.status == AmbiguousPatientMatchStatus.pending
    assert outcome.queued.candidate_patient_name == "Jane Cosmo"
    assert Appointment.where(Appointment.doctor_id == doctor.id).count() == 0


def test_resolve_event_matches_existing_patient_identity():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    PatientExternalIdentity(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=SchedulingProviderSource.acuity,
        external_patient_ref="jane.cosmo@example.com",
    ).save()
    event = _event(external_patient_ref="jane.cosmo@example.com")

    outcome = resolve_event(doctor, event)

    assert outcome.queued is None
    assert outcome.appointment is not None
    assert outcome.appointment.patient_id == patient.id
    assert outcome.appointment.external_id == event.external_appointment_ref
    assert (
        AmbiguousPatientMatch.where(AmbiguousPatientMatch.doctor_id == doctor.id).count()
        == 0
    )


def test_resolve_event_never_fuzzy_matches_by_name():
    """A patient with the exact same name/no identity link must NOT be
    auto-matched — this is the load-bearing safety property of the whole
    feature. Only an exact PatientExternalIdentity hit or an exact
    (doctor_id, external_id) appointment match may auto-resolve."""
    doctor = DoctorFactory.build().save()
    PatientFactory.build(doctor_id=doctor.id, name="Jane Cosmo").save()
    event = _event(external_patient_ref=None, patient_name="Jane Cosmo")

    outcome = resolve_event(doctor, event)

    assert outcome.appointment is None
    assert outcome.queued is not None


def test_resolve_event_updates_existing_appointment_on_redelivery():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    original_start = datetime.now(timezone.utc) + timedelta(days=1)
    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=original_start,
        external_id="acuity-appt-1",
        created_by_system="acuity",
    ).save()

    new_start = original_start + timedelta(hours=2)
    event = _event(external_appointment_ref="acuity-appt-1", start_at=new_start)

    outcome = resolve_event(doctor, event)

    assert outcome.queued is None
    assert outcome.appointment is not None
    assert outcome.appointment.id == appointment.id
    # appointment_date is a naive-timestamp column (matches the rest of the
    # codebase's Appointment usage); compare on value, not tz-awareness.
    assert outcome.appointment.appointment_date.replace(
        tzinfo=timezone.utc
    ) == new_start
    assert Appointment.where(Appointment.doctor_id == doctor.id).count() == 1


def test_confirm_queued_match_creates_appointment_and_identity():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    event = _event(external_patient_ref="jane.cosmo@example.com")
    outcome = resolve_event(doctor, event)
    assert outcome.queued is not None

    appointment = confirm_queued_match(
        outcome.queued, patient=patient, resolved_by="doctor_ref_abc"
    )

    assert appointment.patient_id == patient.id
    assert appointment.external_id == event.external_appointment_ref

    identity = PatientExternalIdentity.get(
        doctor_id=doctor.id,
        source=SchedulingProviderSource.acuity,
        external_patient_ref="jane.cosmo@example.com",
    )
    assert identity is not None
    assert identity.patient_id == patient.id

    resolved_match = AmbiguousPatientMatch.get(id=outcome.queued.id)
    assert resolved_match is not None
    assert resolved_match.status == AmbiguousPatientMatchStatus.resolved
    assert resolved_match.resolved_patient_id == patient.id
    assert resolved_match.resolved_appointment_id == appointment.id
    assert resolved_match.resolved_by == "doctor_ref_abc"


def test_confirm_queued_match_without_external_patient_ref_skips_identity():
    """A confirmed match with no external_patient_ref (source gave nothing
    stable) must not create a bogus PatientExternalIdentity row."""
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    event = _event(external_patient_ref=None, patient_email=None)
    outcome = resolve_event(doctor, event)
    assert outcome.queued is not None

    confirm_queued_match(outcome.queued, patient=patient, resolved_by="doctor_ref_abc")

    assert (
        PatientExternalIdentity.where(
            PatientExternalIdentity.doctor_id == doctor.id
        ).count()
        == 0
    )
