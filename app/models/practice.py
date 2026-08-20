from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field, Relationship

from .doctor import Doctor


class OriginChannel(str, Enum):
    direct = "direct"
    catholic = "catholic"
    mcd = "mcd"


class PracticeRole(str, Enum):
    owner = "owner"
    workforce = "workforce"


class Practice(BaseModel, TimestampsMixin, TypeIDMixin("practice"), table=True):
    """Covered entity. The BAA principal for Cosmas — not the individual clinician."""

    legal_name: str
    npi: str | None = None
    city: str | None = None
    region: str | None = None
    is_solo: bool = True
    join_code: str = Field(unique=True, index=True)
    email_domain: str
    "Workforce must sign in from this domain (or a subdomain) to join."

    origin_channel: str = Field(default="direct")
    pastoral_pack: bool = False

    baa_version: str | None = None
    baa_accepted_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    baa_accepted_by_doctor_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("doctor"), nullable=True),
    )
    baa_signature_name: str | None = None
    baa_signature_title: str | None = None


class PracticeMember(
    BaseModel, TimestampsMixin, TypeIDMixin("practice_member"), table=True
):
    """One Cosmas account belongs to at most one practice."""

    practice_id: TypeIDType = Practice.foreign_key()
    practice: Practice = Relationship()

    doctor_id: TypeIDType = Doctor.foreign_key(unique=True)
    doctor: Doctor = Relationship()

    role: str
    attested_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    attestation_name: str | None = None
