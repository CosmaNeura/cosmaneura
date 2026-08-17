from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .doctor import Doctor
from .patient import Patient

ATHENA_ADAPTER = "athena"
ATHENA_PREVIEW_ENVIRONMENT = "preview"
ATHENA_PREVIEW_PRACTICE = "195900"


class EHRSandboxRecordMapping(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("ehr_sandbox_mapping"),
    table=True,
):
    """Operator-controlled binding to one synthetic sandbox EHR record.

    This table is deliberately narrower than a production EHR authorization
    model.  It can represent only the ratified athenahealth Preview pilot and
    carries its synthetic-only assertion and revocation history durably.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()

    adapter: str = Field(
        default=ATHENA_ADAPTER,
        sa_column=Column(sa.String(32), nullable=False),
    )
    environment: str = Field(
        default=ATHENA_PREVIEW_ENVIRONMENT,
        sa_column=Column(sa.String(32), nullable=False),
    )
    practice_ref: str = Field(
        default=ATHENA_PREVIEW_PRACTICE,
        sa_column=Column(sa.String(32), nullable=False),
    )
    record_ref: str = Field(sa_column=Column(sa.String(160), nullable=False))
    synthetic_only: bool = Field(
        default=True,
        sa_column=Column(sa.Boolean(), nullable=False),
    )

    provisioned_by: str = Field(sa_column=Column(sa.String(160), nullable=False))
    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by: str | None = Field(
        default=None,
        sa_column=Column(sa.String(160), nullable=True),
    )

    def before_save(self) -> None:
        if (
            self.adapter != ATHENA_ADAPTER
            or self.environment != ATHENA_PREVIEW_ENVIRONMENT
            or self.practice_ref != ATHENA_PREVIEW_PRACTICE
            or self.synthetic_only is not True
        ):
            raise ValueError(
                "sandbox mappings are restricted to synthetic athena Preview "
                "practice 195900 records"
            )
        if (self.revoked_at is None) != (self.revoked_by is None):
            raise ValueError("revocation timestamp and actor must be set together")
