import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .appointment import Appointment
from .doctor import Doctor
from .patient import Patient

SYNTHETIC_PREVIEW_FIXTURE_VERSION = "synthetic_preview_labs_v1"


class SyntheticPreviewAppointmentFixture(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("synthetic_preview_fixture"),
    table=True,
):
    """Durable provenance for one operator-created synthetic staging encounter.

    This model is deliberately not exposed by an API route. It lets the
    staging-only operator command be idempotent without searching for a patient
    by name or overloading an external-system identifier.
    """

    __table_args__ = (
        sa.CheckConstraint(
            "fixture_version = 'synthetic_preview_labs_v1'",
            name="ck_synthetic_preview_fixture_version",
        ),
        sa.CheckConstraint(
            "synthetic_only IS TRUE",
            name="ck_synthetic_preview_fixture_synthetic_only",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "doctor_id"],
            ["patient.id", "patient.doctor_id"],
            name="fk_synthetic_preview_fixture_patient_doctor_scope",
        ),
        sa.UniqueConstraint(
            "doctor_id",
            name="uq_synthetic_preview_fixture_doctor",
        ),
        sa.UniqueConstraint(
            "appointment_id",
            name="uq_synthetic_preview_fixture_appointment",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()
    appointment_id: TypeIDType = Appointment.foreign_key()
    fixture_version: str = Field(
        default=SYNTHETIC_PREVIEW_FIXTURE_VERSION,
        sa_column=Column(
            sa.String(64),
            nullable=False,
            server_default=SYNTHETIC_PREVIEW_FIXTURE_VERSION,
        ),
    )
    synthetic_only: bool = Field(
        default=True,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    provisioned_by: str = Field(sa_column=Column(sa.String(160), nullable=False))

    def before_save(self) -> None:
        if (
            self.fixture_version != SYNTHETIC_PREVIEW_FIXTURE_VERSION
            or self.synthetic_only is not True
        ):
            raise ValueError("staging preview fixtures must remain synthetic-only")
