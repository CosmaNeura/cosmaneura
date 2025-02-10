from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Relationship

from .doctor import Doctor


class DoctorNoteFile(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("doctor_note_file"),
    table=True,
):
    """
    When onboarding, doctors will upload PDFs of their best notes.
    This model stores references to the uploaded PDFs in Azure.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    note_type: str
    "user generated name of the note type"

    azure_file_name: str
    "file name of the PDF in azure"
