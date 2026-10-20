"""Medication source dates must describe only owned source appointments."""

from datetime import datetime

from app.routes.internal import _medication_data

from app.models.patient_medication import MedicationSource, PatientMedication

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


def test_owned_extraction_has_source_visit_but_manual_entry_does_not():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    source = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime(2026, 10, 8, 14, 30),
    ).save()
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=source.id,
        name="Synthetic losartan",
        source=MedicationSource.transcript,
    ).save()
    data = _medication_data(row)
    assert data.appointment_id == str(source.id)
    assert data.source_appointment_date == source.appointment_date
    assert not data.confirmed

    row.appointment_id = None
    row.source = MedicationSource.manual
    data = _medication_data(row)
    assert data.appointment_id is None
    assert data.source_appointment_date is None


def test_inconsistent_foreign_source_provenance_is_not_exposed():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    other = DoctorFactory.build().save()
    other_patient = PatientFactory.build(doctor_id=other.id).save()
    source = AppointmentFactory.build(
        doctor_id=other.id, patient_id=other_patient.id,
    ).save()
    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=source.id,
        name="Synthetic losartan",
        source=MedicationSource.transcript,
    ).save()
    data = _medication_data(row)
    assert data.appointment_id is None
    assert data.source_appointment_date is None
