from datetime import datetime, timedelta, timezone
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field, Relationship

from .doctor import Doctor

TRIAL_LENGTH = timedelta(days=14)
"Duration shared by public Checkout and legacy managed trial policies."

ENTITLED_SUBSCRIPTION_STATUSES = {"trialing", "active"}
"Stripe subscription statuses that keep a practice entitled."

KNOWN_SUBSCRIPTION_STATUSES = {
    "incomplete",
    "incomplete_expired",
    "trialing",
    "active",
    "past_due",
    "canceled",
    "unpaid",
    "paused",
    "pending_verification",
    "invalid",
}
"Subscription projections understood by the credential-neutral receiver."


class OriginChannel(str, Enum):
    direct = "direct"
    catholic = "catholic"
    mcd = "mcd"


class PracticeRole(str, Enum):
    owner = "owner"
    workforce = "workforce"


class ClinicianSeatState(str, Enum):
    active = "active"
    inactive = "inactive"


class PracticeAcquisitionSource(str, Enum):
    """Enrollment provenance only; never an authorization decision."""

    managed = "managed"
    public_self_service = "public_self_service"


class Practice(BaseModel, TimestampsMixin, TypeIDMixin("practice"), table=True):
    """Covered entity. The BAA principal for Cosmas — not the individual clinician."""

    __table_args__ = (
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_practice_id_organization_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "email_domain"],
            ["provider_organization.id", "provider_organization.email_domain"],
            name="fk_practice_organization_domain",
        ),
        sa.CheckConstraint(
            "NOT negotiated_baa_required OR ("
            "baa_version IS NULL "
            "AND baa_accepted_at IS NULL "
            "AND baa_accepted_by_doctor_id IS NULL "
            "AND baa_signature_name IS NULL "
            "AND baa_signature_title IS NULL "
            "AND trial_ends_at IS NULL)",
            name="ck_practice_negotiated_baa_required_no_clickwrap",
        ),
        sa.CheckConstraint(
            "acquisition_source IN ('managed', 'public_self_service')",
            name="ck_practice_acquisition_source",
        ),
        sa.CheckConstraint(
            "acquisition_source <> 'public_self_service' OR npi IS NULL",
            name="ck_practice_public_self_service_no_npi",
        ),
        sa.Index("practice_organization_id_idx", "organization_id"),
    )

    legal_name: str
    npi: str | None = None
    city: str | None = None
    region: str | None = None
    is_solo: bool = True
    join_code: str = Field(unique=True, index=True)
    email_domain: str
    "Workforce must sign in from this domain (or a subdomain) to join."
    organization_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("provider_organization"),  # type: ignore
        foreign_key="provider_organization.id",
    )

    origin_channel: str = Field(default="direct")
    pastoral_pack: bool = False
    acquisition_source: str = Field(
        default=PracticeAcquisitionSource.managed.value,
        max_length=32,
    )
    """Enrollment provenance for migration fencing; authorization-neutral."""
    public_pilot_cohort: bool = False
    """Explicit capability-rollout cohort; false is the safe default."""

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
    "Fixed public Checkout trial end, authoritative Stripe end, or legacy BAA trial."

    billing_exempt: bool = False
    "Comped access (early-adopter pilots, future centrally invoiced groups)."
    billing_exempt_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "End of the comp. Null while billing_exempt means exempt indefinitely."

    stripe_customer_id: str | None = Field(default=None, unique=True)
    stripe_subscription_id: str | None = Field(default=None, unique=True)
    subscription_status: str | None = None
    "Mirrored from Stripe webhooks (trialing/active/past_due/canceled/paused)."
    stripe_last_event_created_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "Creation time of the newest Stripe event applied to this practice."
    stripe_last_event_id: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=255), nullable=True),
    )
    stripe_state_version: int = 0
    "Successful authoritative status writes increment this optimistic CAS fence."

    # One deterministic Stripe idempotency key is used per attempt. Keeping the
    # returned hosted URL makes browser retries return the same Checkout Session
    # instead of creating a second subscription before a webhook arrives.
    stripe_checkout_attempt: int = 0
    stripe_checkout_state_version: int = 0
    """CAS fence for hosted-session cache writes/clears."""
    stripe_checkout_session_id: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=255), nullable=True),
    )
    stripe_checkout_session_url: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=2048), nullable=True),
    )
    stripe_checkout_session_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    stripe_checkout_request_json: str | None = Field(
        default=None,
        sa_column=Column(sa.Text(), nullable=True),
    )
    """Durable exact Stripe request body for crash-safe idempotent retries."""
    stripe_checkout_request_created_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    """Start of the bounded Stripe idempotency replay window."""
    stripe_checkout_entitlement_contract: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=64), nullable=True),
    )
    """Exact entitlement contract stamped on the pending Checkout attempt."""
    stripe_trial_clock_policy: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=64), nullable=True),
    )
    """Durable provenance preventing a bound paid-pilot clock downgrade."""
    stripe_subscription_entitlement_contract: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=64), nullable=True),
    )
    """Exact entitlement contract bound to the authoritative subscription."""

    # Membership changes are recorded durably before the best-effort Stripe
    # call. A periodic reconciler keeps retrying until the expected quantity is
    # confirmed, so a transient outage cannot silently underbill forever.
    stripe_quantity_sync_required: bool = False
    stripe_quantity_sync_target: int | None = None
    stripe_quantity_sync_version: int = 0
    "Monotonic fence preventing an older in-flight sync from clearing newer work."
    stripe_quantity_sync_attempts: int = 0
    stripe_quantity_sync_error: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=512), nullable=True),
    )
    stripe_quantity_synced_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )

    negotiated_baa_on_file: bool = False
    "Enterprise fallback: a countersigned CN-BAA-CUST is on file instead of clickwrap."
    negotiated_baa_required: bool = False
    "Fail-closed administrative marker: this practice may never use clickwrap BAA."
    negotiated_baa_effective_date: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    negotiated_baa_reference: str | None = None
    "Document reference for the negotiated instrument (internal-admin set)."

    def negotiated_baa_in_force(self, *, at: datetime | None = None) -> bool:
        """Whether the negotiated instrument itself is effective now."""

        now = at or datetime.now(timezone.utc)
        return bool(
            self.negotiated_baa_on_file
            and self.negotiated_baa_reference
            and self.negotiated_baa_effective_date
            and self.negotiated_baa_effective_date <= now
        )

    def baa_in_force(self, *, at: datetime | None = None) -> bool:
        """Current clickwrap accepted, or an effective negotiated edition.

        A countersigned agreement can be recorded ahead of its effective date,
        but it must not unlock PHI until that date actually arrives. The
        reference is also required so an accidental boolean flip fails closed.
        Clickwrap editions are version-bound: publishing a substantive update
        makes an older clickwrap insufficient until the owner re-accepts.
        """

        if self.negotiated_baa_in_force(at=at):
            return True
        if self.baa_accepted_at is None:
            return False

        # Local import avoids the model cycle: baa.py owns the versioned
        # agreement and references Practice from BaaAcceptance.
        from .baa import BAA_CURRENT_REFERENCE

        return self.baa_version == BAA_CURRENT_REFERENCE

    def billing_exemption_active(self, *, at: datetime | None = None) -> bool:
        """Whether the configured comp is an entitlement source right now."""

        now = at or datetime.now(timezone.utc)
        return self.billing_exempt and (
            self.billing_exempt_until is None or self.billing_exempt_until > now
        )

    def has_authoritative_stripe_subscription_binding(self) -> bool:
        """Whether public billing state is bound to one verified Stripe object."""

        subscription_id = self.stripe_subscription_id
        customer_id = self.stripe_customer_id
        state_version = self.stripe_state_version
        return bool(
            isinstance(subscription_id, str)
            and subscription_id.startswith("sub_")
            and len(subscription_id) > len("sub_")
            and subscription_id.strip() == subscription_id
            and isinstance(customer_id, str)
            and customer_id.startswith("cus_")
            and len(customer_id) > len("cus_")
            and customer_id.strip() == customer_id
            and isinstance(state_version, int)
            and not isinstance(state_version, bool)
            and state_version > 0
        )

    def entitled(self, *, at: datetime | None = None) -> bool:
        """The platform is the system of record for entitlement; Stripe is one
        of several ways it gets set (ARCHITECTURE.md §8, ADR 0016). Fail closed."""

        now = at or datetime.now(timezone.utc)
        status = self.subscription_status
        if status is not None and status not in KNOWN_SUBSCRIPTION_STATUSES:
            return False
        if status in {"pending_verification", "invalid"}:
            return False
        if self.billing_exemption_active(at=now):
            return True
        if (
            self.acquisition_source
            == PracticeAcquisitionSource.public_self_service.value
            and status in ENTITLED_SUBSCRIPTION_STATUSES
            and not self.has_authoritative_stripe_subscription_binding()
        ):
            return False
        if status == "active":
            return True
        if status == "trialing":
            return self.trial_ends_at is not None and self.trial_ends_at > now
        if status is not None:
            # Once an explicit billing projection exists, a stale legacy trial
            # timestamp cannot outrank its non-entitled state.
            return False
        if (
            self.acquisition_source
            == PracticeAcquisitionSource.public_self_service.value
        ):
            # Public acquisition starts its trial at authoritative Checkout;
            # a legacy BAA-time timestamp alone is never paid-pilot authority.
            return False
        return self.trial_ends_at is not None and self.trial_ends_at > now


class PracticeMember(
    BaseModel, TimestampsMixin, TypeIDMixin("practice_member"), table=True
):
    """One Cosmas account belongs to at most one practice."""

    __table_args__ = (
        sa.ForeignKeyConstraint(
            [
                "organization_roster_approval_id",
                "practice_id",
                "doctor_id",
            ],
            [
                "organization_roster_approval.id",
                "organization_roster_approval.target_practice_id",
                "organization_roster_approval.claimed_doctor_id",
            ],
            name="fk_practice_member_organization_claim_binding",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'workforce')",
            name="ck_practice_member_role",
        ),
        sa.CheckConstraint(
            "seat_state IN ('active', 'inactive')",
            name="ck_practice_member_seat_state",
        ),
        sa.CheckConstraint(
            "(organization_roster_approval_id IS NULL "
            "AND organization_authorized_at IS NULL) OR "
            "(organization_roster_approval_id IS NOT NULL "
            "AND organization_authorized_at IS NOT NULL)",
            name="ck_practice_member_organization_authorization_complete",
        ),
        sa.CheckConstraint(
            "(access_revoked_at IS NULL AND access_revoked_by_ref IS NULL "
            "AND access_revocation_reason IS NULL "
            "AND access_records_retain_until IS NULL) OR "
            "(access_revoked_at IS NOT NULL "
            "AND access_revoked_by_ref IS NOT NULL "
            "AND access_revocation_reason IS NOT NULL "
            "AND access_records_retain_until IS NOT NULL "
            "AND access_records_retain_until >= "
            "access_revoked_at + interval '7 years')",
            name="ck_practice_member_revocation_complete",
        ),
        sa.Index(
            "practice_member_access_records_retain_until_idx",
            "access_records_retain_until",
        ),
        sa.Index(
            "practice_member_practice_seat_state_idx",
            "practice_id",
            "seat_state",
        ),
    )

    practice_id: TypeIDType = Practice.foreign_key()
    practice: Practice = Relationship()

    doctor_id: TypeIDType = Doctor.foreign_key(unique=True)
    doctor: Doctor = Relationship()

    role: str
    seat_state: str = Field(default=ClinicianSeatState.active.value)
    "Active, non-revoked memberships occupy one clinician seat; inactive memberships do not."
    attested_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    attestation_name: str | None = None

    organization_roster_approval_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("organization_roster_approval"),
            sa.ForeignKey("organization_roster_approval.id"),
            nullable=True,
            unique=True,
        ),
    )
    organization_authorized_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    access_revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    access_revoked_by_ref: str | None = Field(default=None, max_length=255)
    access_revocation_reason: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=512), nullable=True),
    )
    access_records_retain_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
