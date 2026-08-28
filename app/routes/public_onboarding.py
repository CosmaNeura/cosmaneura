"""Public practice onboarding, invitations, and capability reads.

Every route derives actor and tenant authority from the authenticated Doctor.
No request body can choose a practice, credential state, or capability grant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.routes.middleware import SESSION_SECRET_KEY
from app.services.clerk_identity import verified_primary_email
from app.services.doctor_access import resolve_doctor_access
from app.services.managed_organization_access import uses_managed_organization_path
from app.services.organization_access import (
    OrganizationAccessError,
    doctor_has_managed_roster_assignment,
    lock_roster_enrollment_email,
    member_authorization_basis,
    normalize_email,
)
from app.services.public_capabilities import CapabilityPolicyFacts, capability_decisions
from app.services.public_entitlements import (
    CHECKOUT_TRIAL_CLOCK_POLICY,
    MAX_ACTIVE_CLINICIAN_SEATS,
    PUBLIC_ENTITLEMENT_CONTRACT_VERSION,
    PUBLIC_ENTITLEMENT_POLICY_REVISION,
    ActiveClinicianSeatLimitReached,
    ClinicianSeatPolicyError,
    activate_existing_clinician_seat,
    active_clinician_seat_count,
    has_valid_stripe_subscription_binding,
    lock_practice_for_active_seat_mutation,
    public_subscription_in_force,
    record_active_clinician_seat_quantity,
    require_active_clinician_seat_capacity,
)
from app.services.public_legal import (
    CURRENT_LEGAL_DOCUMENTS,
    PUBLIC_LEGAL_CONTRACT_VERSION,
    current_legal_acceptance_flags,
    legal_submission_is_current,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import (
    ClinicianSeatState,
    CredentialState,
    Practice,
    PracticeMember,
    PracticeRole,
)
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from app.models.public_onboarding import PracticeInvitation, PublicLegalAcceptance
from sqlalchemy import func
from sqlmodel import Session, col, select

public_onboarding_api_app = APIRouter()

PUBLIC_ONBOARDING_CONTRACT_VERSION = "public-onboarding.v1"
_SUBSCRIPTION_STATES = {
    "incomplete",
    "incomplete_expired",
    "pending_verification",
    "invalid",
    "trialing",
    "active",
    "past_due",
    "unpaid",
    "paused",
    "canceled",
}


class OnboardingPractice(BaseModel, extra="forbid"):
    legal_name: str
    role: Literal["owner", "workforce"]


class PublicOnboardingState(BaseModel, extra="forbid"):
    contract_version: Literal["public-onboarding.v1"]
    managed_path: bool
    completed_steps: list[
        Literal["account", "practice", "baa", "legal", "checkout", "credential"]
    ]
    current_step: Literal[
        "practice", "baa", "legal", "checkout", "credential", "complete"
    ]
    practice: OnboardingPractice | None
    baa_accepted: bool
    terms_accepted: bool
    privacy_accepted: bool
    checkout_started: bool
    subscription_state: Literal[
        "none",
        "incomplete",
        "incomplete_expired",
        "pending_verification",
        "invalid",
        "trialing",
        "active",
        "past_due",
        "unpaid",
        "paused",
        "canceled",
    ]
    trial_ends_at: datetime | None
    credential_required: bool
    credential_state: Literal["pending", "approved", "rejected", "needs_review"]
    phi_access_allowed: bool


class LegalDocumentResponse(BaseModel, extra="forbid"):
    kind: Literal["terms", "privacy"]
    version: str
    sha256: str
    path: str


class LegalDocumentsResponse(BaseModel, extra="forbid"):
    contract_version: Literal["public-legal-documents.v1"]
    documents: list[LegalDocumentResponse]


class LegalDocumentSubmission(BaseModel, extra="forbid"):
    kind: Literal["terms", "privacy"]
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AcceptPublicLegalBody(BaseModel, extra="forbid"):
    signature_name: str = Field(min_length=2, max_length=120)
    signature_title: str = Field(min_length=2, max_length=120)
    authority_confirmed: Literal[True]
    documents: list[LegalDocumentSubmission] = Field(min_length=2, max_length=2)

    def normalized_signature(self) -> tuple[str, str]:
        name = self.signature_name.strip()
        title = self.signature_title.strip()
        if len(name) < 2 or len(title) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Signer name and title must contain visible characters.",
            )
        return name, title


class CreateInvitationBody(BaseModel, extra="forbid"):
    email: str = Field(min_length=3, max_length=320)
    expires_in_days: int = Field(default=7, ge=1, le=30)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        try:
            return normalize_email(value)
        except OrganizationAccessError as exc:
            raise ValueError(str(exc)) from exc


class AcceptInvitationBody(BaseModel, extra="forbid"):
    token: str = Field(min_length=32, max_length=128)
    attestation_name: str = Field(min_length=2, max_length=120)
    checks: list[str]

    @field_validator("attestation_name")
    @classmethod
    def validate_attestation_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Attestation name must contain visible characters.")
        return normalized


class RevokeInvitationBody(BaseModel, extra="forbid"):
    reason: str = Field(min_length=3, max_length=512)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Revocation reason must contain visible characters.")
        return normalized


class InvitationResponse(BaseModel, extra="forbid"):
    invitation_id: str
    invitee_email: str
    status: Literal["pending", "accepted", "revoked", "expired"]
    role: Literal["workforce"]
    issued_at: datetime
    expires_at: datetime
    invite_token: str | None
    reused_idempotency_key: bool


class PracticeMemberResponse(BaseModel, extra="forbid"):
    member_id: str
    email: str | None
    role: Literal["owner", "workforce"]
    seat_state: Literal["active", "inactive"]
    credential_state: Literal["pending", "approved", "rejected", "needs_review"]
    access_revoked: bool


CapabilityReasonCode = Literal[
    "allowed",
    "email_unverified",
    "membership_inactive",
    "membership_revoked",
    "legal_incomplete",
    "subscription_inactive",
    "credential_pending",
    "credential_needs_review",
    "credential_rejected",
    "cohort_ineligible",
    "rollout_disabled",
    "policy_unavailable",
]


class CapabilityDecisionResponse(BaseModel, extra="forbid"):
    decision: Literal["allowed", "denied"]
    data_mode: Literal["none", "synthetic_only", "real_phi"]
    rollout: Literal["disabled", "cohort", "enabled"]
    operation_mode: Literal["standard", "read_only", "web_pwa_first", "advisory_only"]
    reason_codes: list[CapabilityReasonCode]

    @model_validator(mode="after")
    def validate_disabled_rollout(self) -> Self:
        if self.rollout == "disabled" and (
            self.decision != "denied"
            or self.data_mode != "none"
            or "rollout_disabled" not in self.reason_codes
            or "allowed" in self.reason_codes
        ):
            raise ValueError(
                "A disabled capability must deny access with no data and a "
                "rollout_disabled reason."
            )
        return self


class CabCapabilityDecisionResponse(BaseModel, extra="forbid"):
    decision: Literal["allowed", "denied"]
    data_mode: Literal["none", "synthetic_only", "real_phi"]
    rollout: Literal["disabled", "cohort", "enabled"]
    operation_mode: Literal["advisory_only"]
    reason_codes: list[CapabilityReasonCode]

    @model_validator(mode="after")
    def validate_disabled_rollout(self) -> Self:
        if self.rollout == "disabled" and (
            self.decision != "denied"
            or self.data_mode != "none"
            or "rollout_disabled" not in self.reason_codes
            or "allowed" in self.reason_codes
        ):
            raise ValueError(
                "A disabled capability must deny access with no data and a "
                "rollout_disabled reason."
            )
        return self


class CapabilityMapResponse(BaseModel, extra="forbid"):
    soap_note: CapabilityDecisionResponse
    intake_prep: CapabilityDecisionResponse
    session_prep: CapabilityDecisionResponse
    scribe: CapabilityDecisionResponse
    cab: CabCapabilityDecisionResponse


class CapabilityActorResponse(BaseModel, extra="forbid"):
    actor_id: str = Field(min_length=1, max_length=255)
    practice_id: str = Field(min_length=1, max_length=255)
    role: Literal["owner", "workforce"]
    membership_state: Literal["active", "inactive", "revoked"]


class CapabilityGatesResponse(BaseModel, extra="forbid"):
    email_verified: bool
    baa_accepted: bool
    terms_accepted: bool
    privacy_accepted: bool
    subscription_state: Literal[
        "none",
        "incomplete",
        "incomplete_expired",
        "pending_verification",
        "invalid",
        "trialing",
        "active",
        "past_due",
        "unpaid",
        "paused",
        "canceled",
    ]
    credential_required: bool
    credential_state: Literal["pending", "approved", "rejected", "needs_review"]


class CapabilitySeatsResponse(BaseModel, extra="forbid"):
    active_clinician_seats: int = Field(ge=0, le=MAX_ACTIVE_CLINICIAN_SEATS)
    maximum_active_clinician_seats: Literal[20]
    pending_invitations: int = Field(ge=0)
    stripe_quantity_sync_required: bool


class CapabilityEntitlementResponse(BaseModel, extra="forbid"):
    contract_version: Literal["public-capability-entitlements.v2"]
    policy_revision: Literal["2026-08-27"]
    generated_at: datetime
    actor: CapabilityActorResponse
    gates: CapabilityGatesResponse
    seats: CapabilitySeatsResponse
    capabilities: CapabilityMapResponse


def _normalized_subscription_state(practice: Practice | None) -> str:
    if practice is None or practice.subscription_status is None:
        return "none"
    if practice.subscription_status in {"active", "trialing"} and not (
        has_valid_stripe_subscription_binding(practice)
    ):
        return "invalid"
    if practice.subscription_status in _SUBSCRIPTION_STATES:
        return practice.subscription_status
    return "invalid"


def _checkout_started(practice: Practice) -> bool:
    # ``trial_ends_at`` is deliberately not evidence that Checkout started.
    # Older Core releases stamped ``trial_ends_at`` at BAA acceptance, so the
    # timestamp alone is not evidence. Priority 1's current policy marker plus
    # its frozen end is durable proof after a real Session is created, even if
    # the expired/abandoned Session cache was later cleared.
    return bool(
        practice.stripe_checkout_session_id
        or practice.subscription_status is not None
        or (
            practice.trial_ends_at is not None
            and getattr(practice, "stripe_trial_clock_policy", None)
            == CHECKOUT_TRIAL_CLOCK_POLICY
        )
    )


def _checkout_completed(practice: Practice) -> bool:
    """Require the canonical paid-path entitlement, not a stale status label."""

    return public_subscription_in_force(practice)


def _live_actor_email(doctor: Doctor) -> str | None:
    try:
        live = verified_primary_email(doctor.clerk_id)
    except Exception:
        return None
    stored = (doctor.email or "").strip().lower()
    normalized = (live or "").strip().lower()
    return normalized if normalized and normalized == stored else None


def _require_direct_actor(request: Request, doctor: Doctor) -> str:
    if (
        request.state.auth_state.payload.get("sub") != doctor.clerk_id
        or request.state.view_as is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This self-service action requires the signed-in account holder.",
        )
    email = _live_actor_email(doctor)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify the current account email, then sign in again.",
        )
    return email


def _current_member(session: Session, doctor: Doctor) -> PracticeMember | None:
    return session.exec(
        select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
    ).one_or_none()


def _record_public_access_audit(
    session: Session,
    *,
    practice: Practice,
    actor: Doctor,
    action: PracticeAccessAction,
    reason: str,
    document_reference: str,
    effective_at: datetime,
) -> None:
    """Append non-PHI evidence in the same transaction as an access change."""

    session.add(
        PracticeAccessAudit(
            practice_id=practice.id,
            action=action.value,
            operator_ref=f"doctor:{actor.id}",
            reason=reason,
            effective_at=effective_at,
            document_reference=document_reference,
        )
    )


def _lock_public_owner(
    session: Session,
    request: Request,
    doctor: Doctor,
    verified_email: str,
) -> tuple[Practice, PracticeMember]:
    member = _current_member(session, doctor)
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Practice required."
        )
    practice = lock_practice_for_active_seat_mutation(session, member.practice_id)
    locked_doctor = session.exec(
        select(Doctor).where(Doctor.id == doctor.id).with_for_update()
    ).one()
    member = session.exec(
        select(PracticeMember).where(PracticeMember.id == member.id).with_for_update()
    ).one()
    if (
        member.practice_id != practice.id
        or locked_doctor.deleted_at is not None
        or request.state.auth_state.payload.get("sub") != locked_doctor.clerk_id
        or not locked_doctor.email
        or not hmac.compare_digest(locked_doctor.email.strip().lower(), verified_email)
        or member.role != PracticeRole.owner.value
        or member.seat_state != ClinicianSeatState.active.value
        or member.access_revoked_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the active practice owner may perform this action.",
        )
    if (
        not practice.public_onboarding_required
        or practice.organization_id is not None
        or practice.negotiated_baa_required
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This practice uses its managed enrollment path.",
        )
    return practice, member


def _onboarding_state(session: Session, doctor: Doctor) -> PublicOnboardingState:
    member = _current_member(session, doctor)
    practice = (
        session.exec(
            select(Practice).where(Practice.id == member.practice_id)
        ).one_or_none()
        if member is not None
        else None
    )
    managed_path = bool(
        (
            practice is not None
            and (
                not practice.public_onboarding_required
                or practice.organization_id is not None
                or practice.negotiated_baa_required
            )
        )
        or (practice is None and uses_managed_organization_path(doctor))
    )
    baa_accepted = bool(practice and practice.baa_in_force())
    terms_accepted = False
    privacy_accepted = False
    if practice is not None:
        terms_accepted, privacy_accepted = current_legal_acceptance_flags(
            session, practice.id
        )
    legal_complete = managed_path or (terms_accepted and privacy_accepted)
    checkout_started = bool(practice and _checkout_started(practice))
    checkout_complete = managed_path or bool(
        practice is not None and _checkout_completed(practice)
    )
    credential_required = bool(practice and practice.public_onboarding_required)
    raw_credential = member.credential_state if member is not None else "pending"
    credential_state = (
        raw_credential
        if raw_credential in {state.value for state in CredentialState}
        else CredentialState.pending.value
    )
    credential_complete = not credential_required or credential_state == "approved"

    completed: list[str] = ["account"]
    if practice is not None and member is not None:
        completed.append("practice")
    if baa_accepted:
        completed.append("baa")
    if legal_complete:
        completed.append("legal")
    if checkout_complete:
        completed.append("checkout")
    if credential_complete:
        completed.append("credential")

    if practice is None or member is None:
        current_step = "practice"
    elif not baa_accepted:
        current_step = "baa"
    elif not legal_complete:
        current_step = "legal"
    elif not checkout_complete:
        current_step = "checkout"
    elif not credential_complete:
        current_step = "credential"
    else:
        current_step = "complete"

    role = member.role if member and member.role in {"owner", "workforce"} else None
    practice_payload = (
        OnboardingPractice(legal_name=practice.legal_name, role=role)  # type: ignore[arg-type]
        if practice is not None and role is not None
        else None
    )
    return PublicOnboardingState(
        contract_version=PUBLIC_ONBOARDING_CONTRACT_VERSION,
        managed_path=managed_path,
        completed_steps=completed,  # type: ignore[arg-type]
        current_step=current_step,  # type: ignore[arg-type]
        practice=practice_payload,
        baa_accepted=baa_accepted,
        terms_accepted=terms_accepted,
        privacy_accepted=privacy_accepted,
        checkout_started=checkout_started,
        subscription_state=_normalized_subscription_state(practice),  # type: ignore[arg-type]
        trial_ends_at=practice.trial_ends_at if practice else None,
        credential_required=credential_required,
        credential_state=credential_state,  # type: ignore[arg-type]
        phi_access_allowed=resolve_doctor_access(doctor).allowed,
    )


@public_onboarding_api_app.get(
    "/public-onboarding",
    name="public_onboarding_state",
    response_model=PublicOnboardingState,
)
def public_onboarding_state(request: Request) -> PublicOnboardingState:
    doctor: Doctor = request.state.doctor
    _require_direct_actor(request, doctor)
    with get_session() as session:
        return _onboarding_state(session, doctor)


@public_onboarding_api_app.get(
    "/legal/current",
    name="public_legal_current",
    response_model=LegalDocumentsResponse,
)
def public_legal_current(request: Request) -> LegalDocumentsResponse:
    _require_direct_actor(request, request.state.doctor)
    return LegalDocumentsResponse(
        contract_version=PUBLIC_LEGAL_CONTRACT_VERSION,
        documents=[
            LegalDocumentResponse(
                kind=document.kind.value,  # type: ignore[arg-type]
                version=document.version,
                sha256=document.sha256,
                path=document.path,
            )
            for document in CURRENT_LEGAL_DOCUMENTS
        ],
    )


@public_onboarding_api_app.post(
    "/practice/legal",
    name="accept_public_legal",
    response_model=PublicOnboardingState,
)
def accept_public_legal(
    request: Request,
    data: AcceptPublicLegalBody,
) -> PublicOnboardingState:
    doctor: Doctor = request.state.doctor
    email = _require_direct_actor(request, doctor)
    signature_name, signature_title = data.normalized_signature()
    submitted = [(item.kind, item.version, item.sha256) for item in data.documents]
    if not legal_submission_is_current(submitted):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The legal documents changed. Reload the current versions.",
        )
    now = datetime.now(timezone.utc)
    with get_session() as session:
        practice, owner = _lock_public_owner(session, request, doctor, email)
        if not practice.baa_in_force(at=now):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Accept the practice BAA before the Terms and Privacy Policy.",
            )
        existing = {
            (row.document_kind, row.document_version, row.content_sha256)
            for row in session.exec(
                select(PublicLegalAcceptance).where(
                    PublicLegalAcceptance.practice_id == practice.id
                )
            ).all()
        }
        authority = "practice owner confirmed authority to bind the covered entity"
        for document in CURRENT_LEGAL_DOCUMENTS:
            key = (document.kind.value, document.version, document.sha256)
            if key in existing:
                continue
            session.add(
                PublicLegalAcceptance(
                    practice_id=practice.id,
                    document_kind=document.kind.value,
                    document_version=document.version,
                    content_sha256=document.sha256,
                    signer_doctor_id=doctor.id,
                    signer_email=email,
                    signer_role=owner.role,
                    signature_name=signature_name,
                    signature_title=signature_title,
                    authority_confirmed=data.authority_confirmed,
                    signer_authority=authority,
                    accepted_at_utc=now,
                )
            )
        session.commit()
        return _onboarding_state(session, doctor)


def _invitation_status(invitation: PracticeInvitation, *, now: datetime) -> str:
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.expires_at <= now:
        return "expired"
    return "pending"


def _invitation_response(
    invitation: PracticeInvitation,
    *,
    token: str | None = None,
    reused: bool = False,
) -> InvitationResponse:
    return InvitationResponse(
        invitation_id=str(invitation.id),
        invitee_email=invitation.invitee_email,
        status=_invitation_status(invitation, now=datetime.now(timezone.utc)),  # type: ignore[arg-type]
        role="workforce",
        issued_at=invitation.issued_at,
        expires_at=invitation.expires_at,
        invite_token=token,
        reused_idempotency_key=reused,
    )


def _email_digest(practice_id: Any, email: str) -> str:
    value = f"public-practice-invite-v1:{practice_id}:{email.strip().lower()}"
    return hmac.new(
        str(SESSION_SECRET_KEY).encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _invitation_token(practice_id: Any, idempotency_key: str, email: str) -> str:
    """Derive a replayable opaque token without persisting bearer material."""

    value = (
        f"public-practice-invite-token-v1:{practice_id}:"
        f"{idempotency_key}:{email.strip().lower()}"
    )
    digest = hmac.new(
        str(SESSION_SECRET_KEY).encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@public_onboarding_api_app.get(
    "/practice/invitations",
    name="practice_invitations",
    response_model=list[InvitationResponse],
)
def practice_invitations(request: Request) -> list[InvitationResponse]:
    doctor: Doctor = request.state.doctor
    email = _require_direct_actor(request, doctor)
    with get_session() as session:
        practice, _ = _lock_public_owner(session, request, doctor, email)
        invitations = session.exec(
            select(PracticeInvitation)
            .where(PracticeInvitation.practice_id == practice.id)
            .order_by(col(PracticeInvitation.issued_at).desc())
        ).all()
        return [_invitation_response(invitation) for invitation in invitations]


@public_onboarding_api_app.post(
    "/practice/invitations",
    name="create_practice_invitation",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_practice_invitation(
    request: Request,
    data: CreateInvitationBody,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
) -> InvitationResponse:
    doctor: Doctor = request.state.doctor
    actor_email = _require_direct_actor(request, doctor)
    invited_email = data.email.strip().lower()
    now = datetime.now(timezone.utc)
    with get_session() as session:
        practice, _ = _lock_public_owner(session, request, doctor, actor_email)
        terms_accepted, privacy_accepted = current_legal_acceptance_flags(
            session, practice.id
        )
        if not (practice.baa_in_force(at=now) and terms_accepted and privacy_accepted):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Complete the practice legal agreements before inviting clinicians.",
            )
        if hmac.compare_digest(invited_email, (doctor.email or "").strip().lower()):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The practice owner already has an active membership.",
            )
        existing_member = session.exec(
            select(PracticeMember)
            .join(Doctor, col(Doctor.id) == col(PracticeMember.doctor_id))
            .where(
                PracticeMember.practice_id == practice.id,
                func.lower(func.btrim(col(Doctor.email))) == invited_email,
            )
        ).first()
        if existing_member is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This account is already a member of the practice.",
            )
        key_digest = hmac.new(
            str(SESSION_SECRET_KEY).encode("utf-8"),
            (
                f"public-practice-invite-idempotency-v1:{practice.id}:{idempotency_key}"
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        email_digest = _email_digest(practice.id, invited_email)
        existing = session.exec(
            select(PracticeInvitation).where(
                PracticeInvitation.practice_id == practice.id,
                PracticeInvitation.idempotency_key_digest == key_digest,
            )
        ).one_or_none()
        if existing is not None:
            if not hmac.compare_digest(
                existing.invitee_email_digest, email_digest
            ) or existing.expires_at - existing.issued_at != timedelta(
                days=data.expires_in_days
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The idempotency key is bound to another invitation.",
                )
            token = _invitation_token(practice.id, idempotency_key, invited_email)
            if not hmac.compare_digest(
                existing.token_digest,
                hashlib.sha256(token.encode("utf-8")).hexdigest(),
            ):
                token = None
            return _invitation_response(existing, token=token, reused=True)

        other_pending = session.exec(
            select(PracticeInvitation)
            .where(
                PracticeInvitation.practice_id == practice.id,
                PracticeInvitation.invitee_email_digest == email_digest,
                col(PracticeInvitation.accepted_at).is_(None),
                col(PracticeInvitation.revoked_at).is_(None),
                PracticeInvitation.expires_at > now,
            )
            .with_for_update()
        ).first()
        if other_pending is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An active invitation already exists for this account.",
            )

        token = _invitation_token(practice.id, idempotency_key, invited_email)
        invitation = PracticeInvitation(
            practice_id=practice.id,
            invited_by_doctor_id=doctor.id,
            token_digest=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            invitee_email=invited_email,
            invitee_email_digest=email_digest,
            idempotency_key_digest=key_digest,
            issued_at=now,
            expires_at=now + timedelta(days=data.expires_in_days),
        )
        # An explicit owner-issued invitation is the transition from solo to
        # group; reusable domain join remains unavailable for public domains.
        practice.is_solo = False
        session.add(practice)
        session.add(invitation)
        _record_public_access_audit(
            session,
            practice=practice,
            actor=doctor,
            action=PracticeAccessAction.public_invitation_issued,
            reason="Owner issued a single-use workforce invitation.",
            document_reference=f"practice_invitation:{invitation.id}",
            effective_at=now,
        )
        session.commit()
        session.refresh(invitation)
        return _invitation_response(invitation, token=token)


@public_onboarding_api_app.post(
    "/practice/invitations/accept",
    name="accept_practice_invitation",
    response_model=PracticeMemberResponse,
)
def accept_practice_invitation(
    request: Request,
    data: AcceptInvitationBody,
) -> PracticeMemberResponse:
    doctor: Doctor = request.state.doctor
    email = _require_direct_actor(request, doctor)
    if uses_managed_organization_path(doctor, verified_primary_email=email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account uses organization-managed enrollment.",
        )
    required_checks = {"workforce", "not-party", "direction", "phi"}
    if not required_checks.issubset(set(data.checks)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All workforce acknowledgements are required.",
        )
    token_digest = hashlib.sha256(data.token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    quantity_changed = False
    with get_session() as session:
        candidate = session.exec(
            select(PracticeInvitation).where(
                PracticeInvitation.token_digest == token_digest
            )
        ).one_or_none()
        if candidate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        practice = lock_practice_for_active_seat_mutation(
            session, candidate.practice_id
        )
        lock_roster_enrollment_email(session, email)
        invitation = session.exec(
            select(PracticeInvitation)
            .where(PracticeInvitation.id == candidate.id)
            .with_for_update()
        ).one()
        if (
            not practice.public_onboarding_required
            or practice.organization_id is not None
            or practice.negotiated_baa_required
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        terms_accepted, privacy_accepted = current_legal_acceptance_flags(
            session, practice.id
        )
        if not (practice.baa_in_force(at=now) and terms_accepted and privacy_accepted):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This invitation is not ready for acceptance.",
            )
        if invitation.invitee_email_digest != _email_digest(practice.id, email):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        locked_doctor = session.exec(
            select(Doctor).where(Doctor.id == doctor.id).with_for_update()
        ).one()
        if (
            locked_doctor.deleted_at is not None
            or request.state.auth_state.payload.get("sub") != locked_doctor.clerk_id
            or not locked_doctor.email
            or not hmac.compare_digest(locked_doctor.email.strip().lower(), email)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The signed-in identity changed. Sign in again.",
            )
        if doctor_has_managed_roster_assignment(
            locked_doctor,
            at=now,
            session=session,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This account uses organization-managed enrollment.",
            )
        member = session.exec(
            select(PracticeMember)
            .where(PracticeMember.doctor_id == locked_doctor.id)
            .with_for_update()
        ).one_or_none()
        if invitation.accepted_at is not None:
            if invitation.accepted_by_doctor_id != locked_doctor.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This invitation has already been used.",
                )
            if member is None or member.practice_id != practice.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The accepted invitation no longer matches membership.",
                )
            # Exact replay returns current membership state. It never revives a
            # seat that the owner subsequently deactivated or revoked.
            session.commit()
            session.refresh(member)
            return _member_response(member, email=email)
        if invitation.revoked_at is not None or invitation.expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This invitation is no longer active.",
            )
        if member is None:
            try:
                require_active_clinician_seat_capacity(session, practice)
            except ActiveClinicianSeatLimitReached as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(exc)
                ) from None
            member = PracticeMember(
                practice_id=practice.id,
                doctor_id=locked_doctor.id,
                role=PracticeRole.workforce.value,
                attested_at=now,
                attestation_name=data.attestation_name.strip(),
            )
            session.add(member)
            quantity_changed = True
        elif member.practice_id != practice.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This account already belongs to another practice.",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This account is already a practice member. Only the practice "
                    "owner may reactivate an inactive seat."
                ),
            )
        invitation.accepted_at = invitation.accepted_at or now
        invitation.accepted_by_doctor_id = (
            invitation.accepted_by_doctor_id or locked_doctor.id
        )
        session.add(invitation)
        if quantity_changed:
            active_seats = record_active_clinician_seat_quantity(session, practice)
            _record_public_access_audit(
                session,
                practice=practice,
                actor=locked_doctor,
                action=PracticeAccessAction.public_invitation_accepted,
                reason=(
                    "Workforce invitation accepted; active clinician seat count "
                    f"is {active_seats}."
                ),
                document_reference=f"practice_invitation:{invitation.id}",
                effective_at=now,
            )
        session.commit()
        session.refresh(practice)
        session.refresh(member)
    return _member_response(member, email=email)


@public_onboarding_api_app.post(
    "/practice/invitations/{invitation_id}/revoke",
    name="revoke_practice_invitation",
    response_model=InvitationResponse,
)
def revoke_practice_invitation(
    request: Request,
    data: RevokeInvitationBody,
    invitation_id: Annotated[TypeIDType, Path()],
) -> InvitationResponse:
    doctor: Doctor = request.state.doctor
    email = _require_direct_actor(request, doctor)
    with get_session() as session:
        practice, _ = _lock_public_owner(session, request, doctor, email)
        invitation = session.exec(
            select(PracticeInvitation)
            .where(PracticeInvitation.id == invitation_id)
            .with_for_update()
        ).one_or_none()
        if invitation is None or invitation.practice_id != practice.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if invitation.accepted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An accepted invitation cannot be revoked.",
            )
        if invitation.revoked_at is None:
            revoked_at = datetime.now(timezone.utc)
            invitation.revoked_at = revoked_at
            invitation.revoked_by_doctor_id = doctor.id
            invitation.revocation_reason = data.reason.strip()
            session.add(invitation)
            _record_public_access_audit(
                session,
                practice=practice,
                actor=doctor,
                action=PracticeAccessAction.public_invitation_revoked,
                reason="Owner revoked an unused workforce invitation.",
                document_reference=f"practice_invitation:{invitation.id}",
                effective_at=revoked_at,
            )
            session.commit()
            session.refresh(invitation)
        return _invitation_response(invitation)


def _member_response(
    member: PracticeMember,
    *,
    email: str | None,
) -> PracticeMemberResponse:
    credential_state = (
        member.credential_state
        if member.credential_state in {state.value for state in CredentialState}
        else CredentialState.pending.value
    )
    return PracticeMemberResponse(
        member_id=str(member.id),
        email=email.strip().lower() if email else None,
        role=member.role,  # type: ignore[arg-type]
        seat_state=member.seat_state,  # type: ignore[arg-type]
        credential_state=credential_state,  # type: ignore[arg-type]
        access_revoked=member.access_revoked_at is not None,
    )


@public_onboarding_api_app.get(
    "/practice/members",
    name="practice_members",
    response_model=list[PracticeMemberResponse],
)
def practice_members(request: Request) -> list[PracticeMemberResponse]:
    doctor: Doctor = request.state.doctor
    email = _require_direct_actor(request, doctor)
    with get_session() as session:
        practice, _ = _lock_public_owner(session, request, doctor, email)
        members = session.exec(
            select(PracticeMember)
            .where(PracticeMember.practice_id == practice.id)
            .order_by(col(PracticeMember.created_at).asc())
        ).all()
        doctor_emails = {
            row.id: row.email
            for row in session.exec(
                select(Doctor).where(
                    col(Doctor.id).in_([member.doctor_id for member in members])
                )
            ).all()
        }
        return [
            _member_response(member, email=doctor_emails.get(member.doctor_id))
            for member in members
        ]


def _set_member_active(
    request: Request,
    member_id: Any,
    *,
    active: bool,
) -> PracticeMemberResponse:
    doctor: Doctor = request.state.doctor
    email = _require_direct_actor(request, doctor)
    quantity_changed = False
    previous_active_count: int | None = None
    with get_session() as session:
        practice, _ = _lock_public_owner(session, request, doctor, email)
        member = session.exec(
            select(PracticeMember)
            .where(PracticeMember.id == member_id)
            .with_for_update()
        ).one_or_none()
        if (
            member is None
            or member.practice_id != practice.id
            or member.role != PracticeRole.workforce.value
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if member.access_revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Revoked access cannot be reactivated through self-service.",
            )
        member_doctor = session.exec(
            select(Doctor).where(Doctor.id == member.doctor_id)
        ).one_or_none()
        member_email = member_doctor.email if member_doctor is not None else None
        if active:
            try:
                quantity_changed = activate_existing_clinician_seat(
                    session, practice, member
                )
            except ActiveClinicianSeatLimitReached as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(exc)
                ) from None
            except ClinicianSeatPolicyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(exc)
                ) from None
        elif member.seat_state == ClinicianSeatState.active.value:
            previous_active_count = active_clinician_seat_count(session, practice.id)
            member.seat_state = ClinicianSeatState.inactive.value
            session.add(member)
            quantity_changed = True
        elif member.seat_state != ClinicianSeatState.inactive.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The membership seat state is invalid.",
            )
        if quantity_changed:
            active_seats = record_active_clinician_seat_quantity(
                session,
                practice,
                previous_active_count=previous_active_count,
            )
            action = (
                PracticeAccessAction.clinician_seat_reactivated
                if active
                else PracticeAccessAction.clinician_seat_deactivated
            )
            _record_public_access_audit(
                session,
                practice=practice,
                actor=doctor,
                action=action,
                reason=(
                    "Owner changed clinician seat state; active clinician seat "
                    f"count is {active_seats}."
                ),
                document_reference=f"practice_member:{member.id}",
                effective_at=datetime.now(timezone.utc),
            )
        session.commit()
        session.refresh(practice)
        session.refresh(member)
    return _member_response(
        member,
        email=member_email,
    )


@public_onboarding_api_app.post(
    "/practice/members/{member_id}/deactivate",
    name="deactivate_practice_member",
    response_model=PracticeMemberResponse,
)
def deactivate_practice_member(
    request: Request,
    member_id: Annotated[TypeIDType, Path()],
) -> PracticeMemberResponse:
    return _set_member_active(request, member_id, active=False)


@public_onboarding_api_app.post(
    "/practice/members/{member_id}/reactivate",
    name="reactivate_practice_member",
    response_model=PracticeMemberResponse,
)
def reactivate_practice_member(
    request: Request,
    member_id: Annotated[TypeIDType, Path()],
) -> PracticeMemberResponse:
    return _set_member_active(request, member_id, active=True)


@public_onboarding_api_app.get(
    "/capabilities",
    name="public_capabilities",
    response_model=CapabilityEntitlementResponse,
)
def public_capabilities(request: Request) -> CapabilityEntitlementResponse:
    doctor: Doctor = request.state.doctor
    _require_direct_actor(request, doctor)
    email_verified = True
    now = datetime.now(timezone.utc)
    with get_session() as session:
        member = _current_member(session, doctor)
        if member is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Complete practice setup before reading capabilities.",
            )
        practice = session.exec(
            select(Practice).where(Practice.id == member.practice_id)
        ).one_or_none()
        if practice is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT)
        if (
            not practice.public_onboarding_required
            or practice.organization_id is not None
            or practice.negotiated_baa_required
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The public capability contract is unavailable for this practice.",
            )
        terms_accepted, privacy_accepted = current_legal_acceptance_flags(
            session, practice.id
        )
        if member.access_revoked_at is not None:
            membership_state = "revoked"
        elif member.seat_state == ClinicianSeatState.active.value:
            membership_state = "active"
        else:
            membership_state = "inactive"
        credential_state = (
            member.credential_state
            if member.credential_state in {state.value for state in CredentialState}
            else CredentialState.pending.value
        )
        pending_invitations = int(
            session.exec(
                select(func.count())
                .select_from(PracticeInvitation)
                .where(
                    PracticeInvitation.practice_id == practice.id,
                    col(PracticeInvitation.accepted_at).is_(None),
                    col(PracticeInvitation.revoked_at).is_(None),
                    PracticeInvitation.expires_at > now,
                )
            ).one()
        )
        seats = active_clinician_seat_count(session, practice.id)
        decisions = capability_decisions(
            CapabilityPolicyFacts(
                email_verified=email_verified,
                membership_state=membership_state,  # type: ignore[arg-type]
                membership_authorized=(
                    member_authorization_basis(member, at=now) is not None
                ),
                baa_accepted=practice.baa_in_force(at=now),
                terms_accepted=terms_accepted,
                privacy_accepted=privacy_accepted,
                legal_required=practice.public_onboarding_required,
                subscription_in_force=public_subscription_in_force(practice, at=now),
                credential_required=practice.public_onboarding_required,
                credential_state=credential_state,  # type: ignore[arg-type]
                public_pilot_cohort=practice.public_pilot_cohort,
            )
        )
        role = member.role if member.role in {"owner", "workforce"} else "workforce"
        return CapabilityEntitlementResponse.model_validate(
            {
                "contract_version": PUBLIC_ENTITLEMENT_CONTRACT_VERSION,
                "policy_revision": PUBLIC_ENTITLEMENT_POLICY_REVISION,
                "generated_at": now,
                "actor": {
                    "actor_id": str(doctor.id),
                    "practice_id": str(practice.id),
                    "role": role,
                    "membership_state": membership_state,
                },
                "gates": {
                    "email_verified": email_verified,
                    "baa_accepted": practice.baa_in_force(at=now),
                    "terms_accepted": terms_accepted,
                    "privacy_accepted": privacy_accepted,
                    "subscription_state": _normalized_subscription_state(practice),
                    "credential_required": practice.public_onboarding_required,
                    "credential_state": credential_state,
                },
                "seats": {
                    "active_clinician_seats": seats,
                    "maximum_active_clinician_seats": MAX_ACTIVE_CLINICIAN_SEATS,
                    "pending_invitations": pending_invitations,
                    "stripe_quantity_sync_required": practice.stripe_quantity_sync_required,
                },
                "capabilities": decisions,
            }
        )
