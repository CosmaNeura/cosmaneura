from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Relationship

from ..doctor import Doctor


class DoctorIntakeFile(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("doctor_intake_file"),
    table=True,
):
    """
    When onboarding, doctors will upload PDFs of their intakes.

    This model stores references to the uploaded PDFs in Azure.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    azure_file_name: str
    "file name of the PDF in azure"
