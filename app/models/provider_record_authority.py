"""Immutable links from current clinical records to the authority kernel."""

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import AuthorityPersonBinding, AuthorityTenant
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice
from sqlmodel import Field


class ProviderPracticeAuthority(
    BaseModel, TypeIDMixin("provider_practice_authority"), table=True
):
    """One immutable provider namespace, owned by a practice or one account."""

    __table_args__ = (
        sa.CheckConstraint(
            "(practice_id IS NOT NULL) <> (doctor_id IS NOT NULL)",
            name="ck_provider_practice_authority_exactly_one_owner",
        ),
    )

    practice_id: TypeIDType | None = Practice.foreign_key(unique=True, nullable=True)
    doctor_id: TypeIDType | None = Doctor.foreign_key(unique=True, nullable=True)
    tenant_id: TypeIDType = AuthorityTenant.foreign_key(unique=True)


class ProviderRecordAuthority(
    BaseModel, TypeIDMixin("provider_record_authority"), table=True
):
    """Scope at issuance; reassignment fails validation instead of retargeting it."""

    binding_id: TypeIDType = AuthorityPersonBinding.foreign_key(unique=True)
    practice_authority_id: TypeIDType = ProviderPracticeAuthority.foreign_key(
        index=True
    )
    doctor_id: TypeIDType = Doctor.foreign_key(index=True)
    appointment_id: TypeIDType = Appointment.foreign_key(unique=True)
    patient_id: TypeIDType = Patient.foreign_key(index=True)
    context_snapshot_ref: str = Field(
        unique=True, max_length=160, exclude=True, repr=False
    )
