"""One-use analysis fence; answers and identity remain in the preparation."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepPatientAnalysis(
    BaseModel, TypeIDMixin("visit_prep_analysis"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint("operation_id", name="uq_vp_patient_analysis_operation"),
        sa.ForeignKeyConstraint(
            ["visit_prep_id"],
            ["visit_prep.id"],
            name="fk_vp_patient_analysis_root",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("expires_at > requested_at", name="analysis_expiry"),
    )

    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    operation_id: UUID
    requested_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    expires_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    claimed: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), server_default=sa.false(), nullable=False),
    )
