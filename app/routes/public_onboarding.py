"""Public self-serve onboarding surface.

Registered only when :func:`public_onboarding_configured` is true, so the whole
lane stays dark until a deployment deliberately enables it.

The preflight this exposes is server-authoritative and PHI-free. Its wire shape
is frozen by ``contracts/public-entitlements/v1/capability-entitlement.schema.json``;
professional credential state and NPI are outside it by ADR 0025 and ADR 0022.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.configuration.clerk import CLERK_PRIVATE_KEY
from app.routes.dependencies.clerk import AuthenticateClerkRequest
from app.routes.dependencies.doctor import inject_doctor_record
from app.services.clerk_identity import verified_primary_email
from app.services.organization_access import member_authorization_basis
from app.services.public_capabilities import (
    CapabilityPolicyFacts,
    capability_decisions,
)
from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    PUBLIC_ENTITLEMENT_CONTRACT_VERSION,
    PUBLIC_ENTITLEMENT_POLICY_REVISION,
    active_clinician_seat_count,
)
from app.services.public_legal import current_legal_acceptance_flags

from activemodel.session_manager import aglobal_session, get_session
from app.models.doctor import Doctor
from app.models.practice import ENTITLED_SUBSCRIPTION_STATUSES, Practice, PracticeMember
from app.models.public_onboarding import PracticeInvitation
from sqlmodel import col, func, select

authenticate_clerk_request_middleware = AuthenticateClerkRequest(CLERK_PRIVATE_KEY)

# Deliberately without `require_practice_in_force`. This endpoint exists to
# report *why* a practice cannot act, so it must stay reachable when the gates
# it reports on are failing. Authentication binds the Clerk subject to the
# Doctor; this preflight separately verifies live identity and membership facts.
public_onboarding_api_app = APIRouter(
    prefix="/public-onboarding/v1",
    tags=["public-onboarding"],
    dependencies=[
        Depends(aglobal_session),
        Depends(authenticate_clerk_request_middleware),
        Depends(inject_doctor_record),
    ],
)


class CapabilityActor(BaseModel, extra="forbid"):
    actor_id: str
    practice_id: str
    role: str
    membership_state: str


class CapabilityGates(BaseModel, extra="forbid"):
    identity_verified: bool
    membership_authorized: bool
    baa_accepted: bool
    terms_accepted: bool
    privacy_accepted: bool
    subscription_state: str
    tenant_isolation_verified: bool


class CapabilitySeats(BaseModel, extra="forbid"):
    active_clinician_seats: int
    maximum_active_clinician_seats: int
    pending_invitations: int
    stripe_quantity_sync_required: bool


class CapabilityEntitlement(BaseModel, extra="forbid"):
    contract_version: str
    policy_revision: str
    generated_at: datetime
    actor: CapabilityActor
    gates: CapabilityGates
    seats: CapabilitySeats
    capabilities: dict[str, dict]


def _pending_invitation_count(session, practice_id, now: datetime) -> int:
    count = session.exec(
        select(func.count())
        .select_from(PracticeInvitation)
        .where(
            PracticeInvitation.practice_id == practice_id,
            col(PracticeInvitation.accepted_at).is_(None),
            col(PracticeInvitation.revoked_at).is_(None),
            PracticeInvitation.expires_at > now,
        )
    ).one()
    return int(count)


@public_onboarding_api_app.get("/capabilities")
def public_onboarding_capabilities(request: Request) -> CapabilityEntitlement:
    """Return the server-authoritative capability preflight for this actor."""

    doctor: Doctor = request.state.doctor
    with get_session() as session:
        return _capability_entitlement(session, doctor)


def _capability_entitlement(session, doctor: Doctor) -> "CapabilityEntitlement":
    member = PracticeMember.get(doctor_id=doctor.id)
    if member is None:
        # No membership means there is no practice to report a decision for.
        # Fail closed rather than inventing a practice-shaped answer.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No practice membership for this account.",
        )

    practice = session.get(Practice, member.practice_id)
    if practice is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Practice record is unavailable.",
        )

    revoked = member.access_revoked_at is not None
    membership_state = "revoked" if revoked else member.seat_state
    terms_accepted, privacy_accepted = current_legal_acceptance_flags(
        session, practice.id
    )

    # A cached Doctor email is not live verification. Use the same normalized,
    # fail-closed identity lookup as privileged enrollment and bind it to this
    # authenticated Doctor before reporting a positive identity gate.
    live_email = verified_primary_email(doctor.clerk_id)
    identity_verified = bool(
        doctor.deleted_at is None
        and doctor.email
        and live_email
        and doctor.email.strip().lower() == live_email
    )
    membership_authorized = member_authorization_basis(member) is not None

    facts = CapabilityPolicyFacts(
        identity_verified=identity_verified,
        membership_state=membership_state,  # type: ignore[arg-type]
        membership_authorized=membership_authorized,
        role=member.role,  # type: ignore[arg-type]
        baa_accepted=practice.baa_in_force(),
        terms_accepted=terms_accepted,
        privacy_accepted=privacy_accepted,
        # The frozen public contract requires an explicit active/trialing
        # projection for an allowed capability. A legacy billing exemption
        # alone must not produce an allowed envelope with state "none".
        subscription_in_force=(
            practice.subscription_status in ENTITLED_SUBSCRIPTION_STATUSES
            and practice.entitled()
        ),
        # The membership row is the only path to this practice, so an actor can
        # never be answered for a tenant other than their own.
        tenant_isolation_verified=member.practice_id == practice.id,
        public_pilot_cohort=practice.public_pilot_cohort,
    )

    generated_at = datetime.now(timezone.utc)
    return CapabilityEntitlement(
        contract_version=PUBLIC_ENTITLEMENT_CONTRACT_VERSION,
        policy_revision=PUBLIC_ENTITLEMENT_POLICY_REVISION,
        generated_at=generated_at,
        actor=CapabilityActor(
            actor_id=str(doctor.id),
            practice_id=str(practice.id),
            role=member.role,
            membership_state=membership_state,
        ),
        gates=CapabilityGates(
            identity_verified=facts.identity_verified,
            membership_authorized=facts.membership_authorized,
            baa_accepted=facts.baa_accepted,
            terms_accepted=facts.terms_accepted,
            privacy_accepted=facts.privacy_accepted,
            subscription_state=practice.subscription_status or "none",
            tenant_isolation_verified=facts.tenant_isolation_verified,
        ),
        seats=CapabilitySeats(
            active_clinician_seats=active_clinician_seat_count(session, practice.id),
            maximum_active_clinician_seats=MAX_ACTIVE_CLINICIAN_SEATS,
            pending_invitations=_pending_invitation_count(
                session, practice.id, generated_at
            ),
            stripe_quantity_sync_required=practice.stripe_quantity_sync_required,
        ),
        capabilities=capability_decisions(facts),
    )
