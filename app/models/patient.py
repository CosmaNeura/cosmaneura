from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin


class Patient(BaseModel, TimestampsMixin, TypeIDMixin("patient"), table=True):
    """
    Represents a patient
    """

    name: str
