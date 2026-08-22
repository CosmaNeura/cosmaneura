from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field, Relationship

from .doctor import Doctor
from .lab_panel import LabPanel
from .patient import Patient


class LabResult(BaseModel, TimestampsMixin, TypeIDMixin("lab_result"), table=True):
    """A single biomarker value within a LabPanel.

    biomarker is a canonical key from app/services/biomarkers.py so the value
    can be joined to its reference range; raw_name preserves what the source
    called it. Content is PHI.
    """

    lab_panel_id: TypeIDType = LabPanel.foreign_key()
    lab_panel: LabPanel = Relationship()

    # denormalized for straightforward per-patient scoped queries
    doctor_id: TypeIDType = Doctor.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()

    biomarker: str = Field(index=True)
    "canonical biomarker key (see biomarkers.py); may be a raw slug if unrecognized"

    raw_name: str
    "the biomarker name exactly as it appeared in the source"

    value: float
    unit: str | None = Field(default=None)
