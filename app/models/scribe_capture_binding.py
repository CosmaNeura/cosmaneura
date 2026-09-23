"""Additive, reviewed capture scope. Identifiers never confer authority alone."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPersonBinding,
    AuthorityTenant,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice
from sqlmodel import Field


class ScribeCaptureProviderLink(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_provider_link"), table=True
):
    """Provision only through a separately reviewed server-side mapping.

    Scope is immutable after review. Revoke the old row and review a new row to
    change it. Existing capture bindings retain their original scope digest.
    """

    __table_args__ = (
        sa.Index(
            "uq_scribe_provider_link_active_actor_tenant",
            "actor_id",
            "tenant_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
            sqlite_where=sa.text("revoked_at IS NULL"),
        ),
    )

    actor_id: TypeIDType = AuthorityActor.foreign_key()
    tenant_id: TypeIDType = AuthorityTenant.foreign_key()
    source_identity_id: TypeIDType = AuthorityActorExternalIdentity.foreign_key()
    doctor_id: TypeIDType = Doctor.foreign_key()
    practice_id: TypeIDType = Practice.foreign_key()
    scribe_tenant_id: UUID
    reviewed_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    review_ref: str = Field(min_length=16, max_length=160)
    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
        nullable=True,  # type: ignore
    )


class ScribeCaptureBinding(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_capture_binding"), table=True
):
    """One creator's immutable encounter binding; never a bearer capability."""

    __table_args__ = (
        sa.UniqueConstraint("workspace_id", name="uq_scribe_capture_workspace"),
        sa.UniqueConstraint(
            "actor_id",
            "tenant_id",
            "appointment_id",
            name="uq_scribe_capture_creator_encounter",
        ),
        sa.CheckConstraint(
            "length(scope_sha256) = 64", name="ck_scribe_capture_scope_digest"
        ),
    )

    workspace_id: UUID
    provider_link_id: TypeIDType = ScribeCaptureProviderLink.foreign_key()
    actor_id: TypeIDType = AuthorityActor.foreign_key()
    tenant_id: TypeIDType = AuthorityTenant.foreign_key()
    source_identity_id: TypeIDType = AuthorityActorExternalIdentity.foreign_key()
    person_binding_id: TypeIDType = AuthorityPersonBinding.foreign_key()
    doctor_id: TypeIDType = Doctor.foreign_key()
    practice_id: TypeIDType = Practice.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()
    appointment_id: TypeIDType = Appointment.foreign_key()
    scribe_tenant_id: UUID
    scope_sha256: str = Field(max_length=64)
    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
        nullable=True,  # type: ignore
    )
