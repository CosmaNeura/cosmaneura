from datetime import datetime, timedelta, timezone
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field, Relationship

from .doctor import Doctor

TRIAL_LENGTH = timedelta(days=14)
"Every practice starts on a free trial, stamped when the BAA is accepted."

ENTITLED_SUBSCRIPTION_STATUSES = {"trialing", "active"}
"Stripe subscription statuses that keep a practice entitled."


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

    trial_ends_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "Stamped as now + TRIAL_LENGTH the first time the BAA is accepted."

    billing_exempt: bool = False
    "Comped access (early-adopter pilots, future centrally invoiced groups)."
    billing_exempt_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "End of the comp. Null while billing_exempt means exempt indefinitely."

    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    subscription_status: str | None = None
    "Mirrored from Stripe webhooks (trialing/active/past_due/canceled/paused)."

    negotiated_baa_on_file: bool = False
    "Enterprise fallback: a countersigned CN-BAA-CUST is on file instead of clickwrap."
    negotiated_baa_effective_date: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    negotiated_baa_reference: str | None = None
    "Document reference for the negotiated instrument (internal-admin set)."

    def baa_in_force(self) -> bool:
        """Clickwrap acceptance recorded, or the negotiated edition on file."""
        return self.baa_accepted_at is not None or self.negotiated_baa_on_file

    def entitled(self) -> bool:
        """The platform is the system of record for entitlement; Stripe is one
        of several ways it gets set (ARCHITECTURE.md §8, ADR 0016). Fail closed."""

        now = datetime.now(timezone.utc)
        if self.billing_exempt and (
            self.billing_exempt_until is None or self.billing_exempt_until > now
        ):
            return True
        if self.subscription_status in ENTITLED_SUBSCRIPTION_STATUSES:
            return True
        return self.trial_ends_at is not None and self.trial_ends_at > now


class PracticeMember(
    BaseModel, TimestampsMixin, TypeIDMixin("practice_member"), table=True
):
    """One Cosmas account belongs to at most one practice."""

    __table_args__ = (
        sa.UniqueConstraint(
            "practice_id",
            "doctor_id",
            name="uq_practice_member_practice_doctor_capture",
        ),
    )

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
