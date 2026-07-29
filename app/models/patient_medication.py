from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor
from .patient import Patient


class MedicationKind(str, Enum):
    prescription = "prescription"
    otc = "otc"
    supplement = "supplement"
    unknown = "unknown"


class MedicationStatus(str, Enum):
    active = "active"
    stopped = "stopped"


class MedicationSource(str, Enum):
    transcript = "transcript"
    intake = "intake"
    manual = "manual"


class PatientMedication(
    BaseModel,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("patient_medication"),
    table=True,
):
    """A single medication or supplement a patient is taking.

    Rows are seeded by an extraction job that reads visit transcripts and intake
    data, then confirmed/edited by the provider. This is the structured
    foundation the interaction / nutrient-depletion checks build on — those
    tools need a normalized "what is this patient taking" list, which Cosmas
    otherwise only has as free text buried in transcripts.

    Content is PHI: logs carry IDs only.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    appointment_id: TypeIDType | None = Appointment.foreign_key(nullable=True)
    "the appointment whose transcript/intake this was extracted from, if any"

    name: str
    "verbatim name as spoken/entered, e.g. 'metformin 500mg' or 'fish oil'"

    normalized_name: str | None = Field(default=None)
    "cleaned drug/ingredient name for lookups, e.g. 'metformin'"

    rxnorm_cui: str | None = Field(default=None)
    "RxNorm concept id once normalized (NLM, public domain); null for supplements without one"

    kind: MedicationKind = Field(default=MedicationKind.unknown)
    status: MedicationStatus = Field(default=MedicationStatus.active)
    source: MedicationSource = Field(default=MedicationSource.manual)

    dosage: str | None = Field(default=None)
    "free-text dose/frequency as captured, e.g. '500mg twice daily'"

    confirmed: bool = Field(default=False)
    "extracted rows start unconfirmed; the provider confirms or edits before checks run against them"

    confirmed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "set when the provider confirms the row"
