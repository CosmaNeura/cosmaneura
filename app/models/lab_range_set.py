from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field

from .doctor import Doctor


class LabRangeSet(BaseModel, TimestampsMixin, TypeIDMixin("lab_range_set"), table=True):
    """A provider's override of the standard/optimal range for one biomarker.

    Platform defaults live in app/services/lab_ranges.py; a row here overrides
    the default for this doctor (this is where the provider's own
    functional-medicine reference values go). No PHI — biomarker keys, units,
    numeric bounds, and an optional source link only.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()

    biomarker: str = Field(index=True)
    "canonical biomarker key (see biomarkers.py)"

    unit: str | None = Field(default=None)

    standard_low: float | None = Field(default=None)
    standard_high: float | None = Field(default=None)
    optimal_low: float | None = Field(default=None)
    optimal_high: float | None = Field(default=None)

    source_url: str | None = Field(default=None)
    "where the provider sourced these values (e.g. their functional-medicine reference)"
