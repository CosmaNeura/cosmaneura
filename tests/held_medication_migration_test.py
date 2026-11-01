"""The additive status value and patient rows survive the supported recovery rung."""

from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_engine
from app.models.patient_medication import MedicationStatus, PatientMedication
from sqlalchemy import text

from tests.factories import DoctorFactory, PatientFactory


def test_enum_expand_recovery_retains_held_and_legacy_patient_rows():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    rows = [
        PatientMedication(
            doctor_id=doctor.id,
            patient_id=patient.id,
            name="Synthetic enum recovery medication",
            status=value,
            confirmed=True,
        ).save()
        for value in MedicationStatus
    ]
    expected = {str(row.id): row.status for row in rows}
    config = Config("alembic.ini")

    def catalog():
        with get_engine().connect() as connection:
            return (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one(),
                connection.execute(
                    text(
                        "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid=enumtypid "
                        "WHERE typname='medicationstatus' ORDER BY enumsortorder"
                    )
                )
                .scalars()
                .all(),
            )

    assert catalog() == ("312b217ebba4", ["active", "stopped", "held"])
    try:
        command.downgrade(config, "d4de1ee4bb02")
        assert catalog() == ("d4de1ee4bb02", ["active", "stopped", "held"])
        assert {
            str(row.id): PatientMedication.one(id=row.id).status for row in rows
        } == expected
    finally:
        command.upgrade(config, "312b217ebba4")
    assert catalog() == ("312b217ebba4", ["active", "stopped", "held"])
    assert {
        str(row.id): PatientMedication.one(id=row.id).status for row in rows
    } == expected
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 3
    )
