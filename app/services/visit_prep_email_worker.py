"""Dormant single-submission email worker and secret-only maintenance.

Commit the irreversible claim before calling a transport. Reacquire current
authority and hold its fences through the bounded submission. A crash or lost
acknowledgment never reopens the send lane. The scheduled job and provider route
remain default off; the job selects Core SMTP only after source qualification.
The standard entrypoints resolve centrally managed practice timing and fence its
version through dispatch. Explicit-policy domain calls remain compatible for
isolated tests. Neither path is a scheduler or new sending authorization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Literal, Protocol, cast

from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.configuration.visit_prep_cadence import current_standard_cadence
from app.services.visit_prep_command_contract import access_token_hash
from app.services.visit_prep_current_timing import (
    CurrentTimingPlan,
    plan_locked_current_delivery,
)
from app.services.visit_prep_delivery_timing import (
    DeliveryTimingInvalid,
    TimingDecision,
    plan_delivery_timing,
    validate_delivery_timing_policy,
)
from app.services.visit_prep_email_authority import (
    EmailConsentSourceVerifier,
    LockedEmailAuthority,
    resolve_locked_email_authority,
)
from app.services.visit_prep_email_contract import (
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
    _patient_origin,
)
from app.services.visit_prep_email_enqueue import (
    _check_replay,
    _context,
    _json,
    _request_digest,
)
from app.services.visit_prep_email_evidence import _digest
from app.services.visit_prep_email_payload import (
    _canonical_ref,
    _closed_object,
    bind_stored_invitation,
    decrypt_invitation_payload,
    invitation_authorization_digest,
)
from app.services.visit_prep_email_policy import _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import VisitPrepError, _lock_current_lineage
from app.utils.encryption import decrypt_value

from activemodel.session_manager import global_session
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from app.models.visit_prep_email_authority import (
    VisitPrepEmailApprovalEvent,
    VisitPrepEmailConsentEvent,
)
from sqlmodel import Session, and_, col, func, or_, select

Outcome = Literal[
    "idle",
    "wait",
    "disabled",
    "accepted",
    "suppressed",
    "failed",
    "uncertain",
    "discarded",
    "storage_unavailable",
]
_TimingPolicy = tuple[tuple[int, int], timedelta]


class InvitationTransport(Protocol):
    def __call__(self, invitation: VisitPrepInvitationEmail, /) -> Literal["accepted"]:
        """Trusted Core adapter: one attempt, at most 30 seconds, no retry.

        The existing CoreSMTPInvitationTransport implements this boundary.
        Exceptions and unknown results mean uncertain once invocation begins.
        It must not use/commit the worker's transaction or log private values.
        A fake is injected only by synthetic tests, never a browser request.
        """
        ...


@dataclass(repr=False)
class _ClaimCustody:
    lock: Any = field(default_factory=Lock)
    used: bool = False
    timing_policy: _TimingPolicy | None = None
    standard_cadence_fingerprint: str | None = None


@dataclass(frozen=True, repr=False)
class EmailSubmissionClaim:
    delivery_id: TypeIDType
    visit_prep_id: TypeIDType
    operation_id: str
    submitted_at: datetime
    lease_until: datetime
    _custody: _ClaimCustody = field(
        default_factory=_ClaimCustody, compare=False, repr=False
    )

    def consume(self) -> bool:
        # Process-local one-use custody complements the durable send lane.
        # Lock makes the claim nonserializable; no broker/API receives it.
        with self._custody.lock:
            if self._custody.used:
                return False
            self._custody.used = True
            return True

    def __repr__(self) -> str:
        return "EmailSubmissionClaim(<redacted>)"


def _enabled(settings: VisitPrepSettings) -> bool:
    return (
        type(settings) is VisitPrepSettings
        and settings.configuration_valid is True
        and settings.enabled is True
        and settings.public_api_enabled is True
        and settings.email_send_enabled is True
    )


def _timing_policy(
    quiet_hours: tuple[int, int] | None, minimum_channel_spacing: timedelta | None
) -> _TimingPolicy | None:
    if quiet_hours is None or minimum_channel_spacing is None:
        return None
    try:
        validate_delivery_timing_policy(
            quiet_hours=quiet_hours, minimum_channel_spacing=minimum_channel_spacing
        )
    except DeliveryTimingInvalid:
        return None
    return quiet_hours, minimum_channel_spacing


def _standard_claim_current(claim: EmailSubmissionClaim) -> bool:
    fingerprint = claim._custody.standard_cadence_fingerprint
    if fingerprint is None:
        return True  # Existing explicit-policy domain calls remain compatible.
    current = current_standard_cadence()
    return current is not None and current.fingerprint == fingerprint


def _cadence_decision(
    plan: CurrentTimingPlan, *, policy: _TimingPolicy, checked_at: datetime
) -> TimingDecision:
    return plan_delivery_timing(
        plan.snapshot,
        now=checked_at,
        quiet_hours=policy[0],
        minimum_channel_spacing=policy[1],
        history=plan.email_history,
    )[0]


def _current_cadence(
    session: Session,
    prep: VisitPrep,
    row: VisitPrepEmailDelivery,
    *,
    locked: LockedEmailAuthority,
    scope: dict,
    policy: _TimingPolicy,
) -> tuple[CurrentTimingPlan, TimingDecision]:
    plan = plan_locked_current_delivery(
        session,
        **scope,
        visit_prep_id=prep.id,
        quiet_hours=policy[0],
        minimum_channel_spacing=policy[1],
    )
    own = [
        touch
        for touch in plan.email_history
        if touch.step == "booking_email"
        and touch.credential_generation == row.credential_generation
        and touch.schedule_generation == plan.snapshot.schedule_generation
    ]
    if (
        plan.preparation_id != str(row.visit_prep_id)
        or row.credential_generation != plan.snapshot.credential_generation
        or row.schedule_generation_id != locked.credential.schedule_generation_id
        or len(own) != 1
        or own[0].state != row.state
        or own[0].requested_at != row.requested_at
        or own[0].submitted_at != row.submitted_at
        or row.state not in {"queued", "submitting"}
    ):
        raise VisitPrepEmailInvalid()
    # The loader validated ALL retained history first. Exclude only this exact
    # current invitation (unique per root/credential), not other generations'
    # uncertain/pending submissions or their channel-spacing timestamps.
    plan = replace(
        plan,
        email_history=tuple(
            touch for touch in plan.email_history if touch is not own[0]
        ),
    )
    _, _, now = _authorize_owner(session, **scope)
    deadlines = [
        row.expires_at,
        locked.credential.expires_at,
        locked.approval.expires_at,
        locked.consent.expires_at,
    ]
    if row.state == "submitting":
        deadlines.append(row.lease_until)
    if any(deadline is None or now >= deadline for deadline in deadlines):
        raise VisitPrepEmailInvalid()
    # Source/approval fences are still held. Recheck their expiry after cadence
    # query waits; the planner itself is never authority to send.
    return plan, _cadence_decision(plan, policy=policy, checked_at=now)


def _lock_rows(
    session: Session, identifier: TypeIDType
) -> tuple[VisitPrep, VisitPrepEmailDelivery] | None:
    # Read routing hints first, then serialize Practice -> root -> delivery.
    # Cleanup must remain possible even after owner/BAA/entitlement withdrawal;
    # these row locks are custody fences, not sending permission.
    hints = session.exec(
        select(col(VisitPrepEmailDelivery.visit_prep_id), col(VisitPrep.practice_id))
        .join(VisitPrep, col(VisitPrep.id) == col(VisitPrepEmailDelivery.visit_prep_id))
        .where(VisitPrepEmailDelivery.id == identifier)
    ).one_or_none()
    if hints is None:
        return None
    session.exec(
        select(Practice).where(Practice.id == hints[1]).with_for_update()
    ).one()
    prep = session.exec(
        select(VisitPrep)
        .where(VisitPrep.id == hints[0])
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    row = session.exec(
        select(VisitPrepEmailDelivery)
        .where(VisitPrepEmailDelivery.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None or row.visit_prep_id != prep.id or prep.practice_id != hints[1]:
        return None
    return prep, row


def _authorization(row: VisitPrepEmailDelivery, prep: VisitPrep) -> dict:
    invitation_authorization_digest(row.authorization_ciphertext)
    plaintext = decrypt_value(row.authorization_ciphertext)
    if len(plaintext.encode("utf-8")) > 5_500:
        raise VisitPrepEmailInvalid()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"context", "request_digest", "subject", "approval", "consent"}
        or _json(payload["context"]) != _json(_context(row, prep))
        or not isinstance(payload["subject"], str)
        or not 1 <= len(payload["subject"]) <= 255
        or not _digest(payload["request_digest"])
    ):
        raise VisitPrepEmailInvalid()
    return payload


def _eligible(
    session: Session,
    prep: VisitPrep,
    row: VisitPrepEmailDelivery,
    *,
    patient_origin: str,
    source_verifier: EmailConsentSourceVerifier,
    settings: VisitPrepSettings,
) -> tuple[LockedEmailAuthority, VisitPrepInvitationEmail, dict]:
    evidence = _authorization(row, prep)
    scope = {
        "owner_practice_member_id": prep.owner_practice_member_id,
        "owner_doctor_id": prep.owner_doctor_id,
        "practice_id": prep.practice_id,
        "expected_clerk_subject": evidence["subject"],
        "settings": settings,
    }
    authority_scope = {
        **scope,
        "visit_prep_id": prep.id,
        "source_verifier": source_verifier,
    }
    locked = resolve_locked_email_authority(session, **authority_scope)
    if (
        _json(evidence["approval"]) != _json(locked.approval.manifest())
        or _json(evidence["consent"]) != _json(locked.consent.manifest())
        or row.payload_ciphertext is None
    ):
        raise VisitPrepEmailInvalid()
    invitation = decrypt_invitation_payload(
        row.payload_ciphertext,
        bind_stored_invitation(
            row, prep, locked.credential, checked_at=locked.checked_at
        ),
        patient_origin=patient_origin,
    )
    _check_replay(
        row,
        prep,
        subject=evidence["subject"],
        request_digest=_request_digest(
            prep,
            generation=row.credential_generation,
            token_hash=access_token_hash(invitation.access_token),
            patient_origin=patient_origin,
        ),
        operation_id=str(row.operation_id),
        generation=row.credential_generation,
    )
    # Source/expiry recheck follows all decryption, and precedes command writes
    # or the provider call. Source callbacks may never release these fences.
    current = resolve_locked_email_authority(session, **authority_scope)
    if (
        current.approval != locked.approval
        or current.consent != locked.consent
        or current.credential.generation != row.credential_generation
        or current.checked_at >= row.expires_at
        or (
            row.state == "submitting"
            and (row.lease_until is None or current.checked_at >= row.lease_until)
        )
    ):
        raise VisitPrepEmailInvalid()
    return current, invitation, scope


def _audit(
    session: Session, prep: VisitPrep, row: VisitPrepEmailDelivery, outcome: str
) -> None:
    session.add(
        AuthorityAuditEvent(
            event_type=f"visit_prep.email_{outcome}",
            outcome=outcome,
            occurred_at=_database_now(session),
            client_event_id=row.operation_id,
            resource_type="visit_prep_email",
            resource_ref=str(row.id),
            route="visit_prep_email_worker",
            intent="invitation_email_submission",
            platform_refs=[
                {"kind": "doctor", "ref": str(prep.owner_doctor_id)},
                {"kind": "practice", "ref": str(prep.practice_id)},
                {"kind": "practice_member", "ref": str(prep.owner_practice_member_id)},
                {"kind": "visit_prep", "ref": str(prep.id)},
            ],
        )
    )


def _finish(
    session: Session,
    prep: VisitPrep,
    row: VisitPrepEmailDelivery,
    *,
    state: Literal["accepted", "failed", "suppressed", "uncertain"],
    code: str | None,
) -> Outcome:
    now = _database_now(session)
    if row.state == "submitting" and (
        row.lease_until is None or now >= row.lease_until
    ):
        state, code = "uncertain", "submission_uncertain"
    row.state, row.outcome_code = state, code
    row.payload_ciphertext, row.lease_until, row.finalized_at = None, None, now
    session.add(row)
    session.flush()
    _audit(session, prep, row, state)
    session.flush()
    session.commit()
    return state


def claim_email_invitation(
    *,
    patient_origin: str,
    quiet_hours: tuple[int, int] | None = None,
    minimum_channel_spacing: timedelta | None = None,
    source_verifier: EmailConsentSourceVerifier | None = None,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> EmailSubmissionClaim | Outcome:
    """Commit one irreversible claim, without returning or sending a bearer.

    Uses its own global session and rejects an ambient global transaction.
    Lost claim acknowledgment yields storage_unavailable, never a provider call.
    No expired submitting row is reclaimed: maintenance alone finalizes it.
    Missing/invalid cadence inputs disable claiming. Wait leaves the exact
    queued intent unchanged; other candidates in this bounded batch are checked.
    """
    policy = _timing_policy(quiet_hours, minimum_channel_spacing)
    if (
        not _enabled(settings)
        or policy is None
        or source_verifier is None
        or not callable(source_verifier)
    ):
        return "disabled"
    try:
        patient_origin = _patient_origin(patient_origin)
        with global_session() as session:
            identifiers = session.exec(
                select(col(VisitPrepEmailDelivery.id))
                .where(VisitPrepEmailDelivery.state == "queued")
                .order_by(
                    col(VisitPrepEmailDelivery.requested_at),
                    col(VisitPrepEmailDelivery.id),
                )
                .limit(16)
            ).all()
            waiting = False
            for identifier in identifiers:
                pair = _lock_rows(session, identifier)
                if pair is None or pair[1].state != "queued":
                    session.rollback()
                    continue
                prep, row = pair
                if _database_now(session) >= row.expires_at:
                    return _finish(
                        session, prep, row, state="suppressed", code="expired"
                    )
                try:
                    locked, _, scope = _eligible(
                        session,
                        prep,
                        row,
                        patient_origin=patient_origin,
                        source_verifier=source_verifier,
                        settings=settings,
                    )
                    plan, cadence = _current_cadence(
                        session, prep, row, locked=locked, scope=scope, policy=policy
                    )
                except (VisitPrepEmailInvalid, VisitPrepError, ValueError):
                    # A verifier that released a transaction cannot authorize
                    # even a suppression write with this now-unlocked snapshot.
                    session.rollback()
                    pair = _lock_rows(session, identifier)
                    if pair is None or pair[1].state != "queued":
                        return "discarded"
                    return _finish(
                        session, *pair, state="suppressed", code="scope_unavailable"
                    )
                if cadence.action == "wait":
                    waiting = True
                    session.rollback()
                    continue
                if cadence.action != "candidate":
                    return _finish(
                        session, prep, row, state="suppressed", code="obsolete_context"
                    )
                row.state, row.submitted_at = "submitting", locked.checked_at
                row.lease_until = locked.checked_at + timedelta(seconds=90)
                session.add(row)
                session.flush()
                session.refresh(row)  # database owns the submission clock/lease
                _audit(session, prep, row, "submission_claimed")
                session.flush()
                _, _, checked_at = _authorize_owner(session, **scope)
                if (
                    row.submitted_at is None
                    or row.lease_until is None
                    or locked.approval.expires_at is None
                    or locked.consent.expires_at is None
                    or checked_at
                    >= min(
                        row.expires_at,
                        row.lease_until,
                        locked.credential.expires_at,
                        locked.approval.expires_at,
                        locked.consent.expires_at,
                    )
                ):
                    session.rollback()
                    return "storage_unavailable"
                if (
                    _cadence_decision(plan, policy=policy, checked_at=checked_at).action
                    != "candidate"
                ):
                    session.rollback()
                    return "wait"
                claim = EmailSubmissionClaim(
                    row.id,
                    prep.id,
                    str(row.operation_id),
                    row.submitted_at,
                    row.lease_until,
                    _custody=_ClaimCustody(timing_policy=policy),
                )
                session.commit()
                return claim
            return "wait" if waiting else "idle"
    except Exception:
        # SQL, source and custody diagnostics may contain bound secrets. No
        # exception or provider data is transported into a task failure store.
        return "storage_unavailable"


def dispatch_email_claim(
    claim: EmailSubmissionClaim,
    *,
    patient_origin: str,
    quiet_hours: tuple[int, int] | None = None,
    minimum_channel_spacing: timedelta | None = None,
    transport: InvitationTransport | None = None,
    source_verifier: EmailConsentSourceVerifier | None = None,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> Outcome:
    """Only the original successful claim invocation may call this once.

    Do not serialize/replay a claim or expose it through an API. The worker
    retains custody locks through the transport's bounded 30-second attempt.
    A successful SMTP acceptance is not confirmed recipient delivery.
    Require the claim's same explicit restrictions and current cadence. A
    delayed claim that is no longer permitted is suppressed, never requeued.
    """
    if (
        type(claim) is not EmailSubmissionClaim
        or not _canonical_ref(str(claim.delivery_id), "visit_prep_email")
        or not claim.consume()
    ):
        return "discarded"
    policy = _timing_policy(quiet_hours, minimum_channel_spacing)
    try:
        with global_session() as session:
            pair = _lock_rows(session, claim.delivery_id)
            if pair is None:
                return "discarded"
            prep, row = pair
            if (
                row.state != "submitting"
                or prep.id != claim.visit_prep_id
                or str(row.operation_id) != claim.operation_id
                or row.submitted_at != claim.submitted_at
                or row.lease_until != claim.lease_until
            ):
                return "discarded"
            if _database_now(session) >= min(row.expires_at, claim.lease_until):
                return _finish(
                    session, prep, row, state="uncertain", code="submission_uncertain"
                )
            if (
                not _enabled(settings)
                or policy is None
                or policy != claim._custody.timing_policy
                or not _standard_claim_current(claim)
                or source_verifier is None
                or not callable(source_verifier)
            ):
                return _finish(
                    session, prep, row, state="suppressed", code="scope_unavailable"
                )
            if transport is None or not callable(transport):
                return _finish(
                    session, prep, row, state="failed", code="transport_unavailable"
                )
            try:
                locked, invitation, scope = _eligible(
                    session,
                    prep,
                    row,
                    patient_origin=_patient_origin(patient_origin),
                    source_verifier=source_verifier,
                    settings=settings,
                )
                _, cadence = _current_cadence(
                    session, prep, row, locked=locked, scope=scope, policy=policy
                )
            except (VisitPrepEmailInvalid, VisitPrepError, ValueError):
                session.rollback()
                pair = _lock_rows(session, claim.delivery_id)
                if (
                    pair is None
                    or pair[1].state != "submitting"
                    or pair[1].submitted_at != claim.submitted_at
                ):
                    return "discarded"
                return _finish(
                    session, *pair, state="suppressed", code="scope_unavailable"
                )
            if cadence.action != "candidate" or not _standard_claim_current(claim):
                # A committed claim is never returned to queued, even if quiet
                # hours began during the claim/dispatch gap. No effect occurred;
                # consume custody and clear the secret without an automatic retry.
                return _finish(
                    session, prep, row, state="suppressed", code="scope_unavailable"
                )
            transaction, nested = (
                session.get_transaction(),
                session.get_nested_transaction(),
            )
            accepted = False
            try:
                response = transport(invitation)
                accepted = type(response) is str and response == "accepted"
            except Exception:
                pass
            if (
                transaction is None
                or not transaction.is_active
                or session.get_transaction() is not transaction
                or session.get_nested_transaction() is not nested
            ):
                return "storage_unavailable"
            return _finish(
                session,
                prep,
                row,
                state="accepted" if accepted else "uncertain",
                code=None if accepted else "submission_uncertain",
            )
    except Exception:
        return "storage_unavailable"


def run_one_email(
    *,
    patient_origin: str,
    quiet_hours: tuple[int, int] | None = None,
    minimum_channel_spacing: timedelta | None = None,
    transport: InvitationTransport | None = None,
    source_verifier: EmailConsentSourceVerifier | None = None,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> Outcome:
    """Drain one invitation, never autoretry or resume an earlier send claim."""
    claimed = claim_email_invitation(
        patient_origin=patient_origin,
        quiet_hours=quiet_hours,
        minimum_channel_spacing=minimum_channel_spacing,
        source_verifier=source_verifier,
        settings=settings,
    )
    if not isinstance(claimed, EmailSubmissionClaim):
        return claimed
    return dispatch_email_claim(
        claimed,
        patient_origin=patient_origin,
        quiet_hours=quiet_hours,
        minimum_channel_spacing=minimum_channel_spacing,
        transport=transport,
        source_verifier=source_verifier,
        settings=settings,
    )


def claim_standard_email_invitation(
    *,
    patient_origin: str,
    source_verifier: EmailConsentSourceVerifier | None = None,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> EmailSubmissionClaim | Outcome:
    """Use platform-managed practice timing, never caller/provider overrides."""
    cadence = current_standard_cadence()
    if cadence is None:
        return "disabled"
    result = claim_email_invitation(
        patient_origin=patient_origin,
        quiet_hours=cadence.quiet_hours,
        minimum_channel_spacing=cadence.minimum_channel_spacing,
        source_verifier=source_verifier,
        settings=settings,
    )
    if type(result) is EmailSubmissionClaim:
        result._custody.standard_cadence_fingerprint = cadence.fingerprint
    return result


def dispatch_standard_email_claim(
    claim: EmailSubmissionClaim,
    *,
    patient_origin: str,
    transport: InvitationTransport | None = None,
    source_verifier: EmailConsentSourceVerifier | None = None,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> Outcome:
    """Re-read the standard before dispatch; a changed claim never sends."""
    cadence = current_standard_cadence()
    # Missing/changed policy still consumes and finalizes an actual owned claim.
    # Never leave it available for a later retry using a different policy.
    if type(claim) is not EmailSubmissionClaim:
        return "discarded"
    if claim._custody.standard_cadence_fingerprint is None:
        return "discarded"
    return dispatch_email_claim(
        claim,
        patient_origin=patient_origin,
        quiet_hours=cadence.quiet_hours if cadence is not None else None,
        minimum_channel_spacing=cadence.minimum_channel_spacing
        if cadence is not None
        else None,
        transport=transport,
        source_verifier=source_verifier,
        settings=settings,
    )


def run_one_standard_email(
    *,
    patient_origin: str,
    transport: InvitationTransport | None = None,
    source_verifier: EmailConsentSourceVerifier | None = None,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> Outcome:
    """Dormant worker entry using one centrally controlled timing source."""
    claimed = claim_standard_email_invitation(
        patient_origin=patient_origin,
        source_verifier=source_verifier,
        settings=settings,
    )
    if not isinstance(claimed, EmailSubmissionClaim):
        return claimed
    return dispatch_standard_email_claim(
        claimed,
        patient_origin=patient_origin,
        transport=transport,
        source_verifier=source_verifier,
        settings=settings,
    )


def maintain_email_secrets(*, limit: int = 25) -> dict[str, int | str]:
    """Erase obsolete/expired payloads even with release/channel gates off.

    Only the transient invitation ciphertext is cleared. Keep authorization,
    immutable evidence, patient words and legal holds. This is not the still
    unqualified consent-evidence retention/aggregate purge workflow.
    Candidate selection includes passed appointments and obsolete lineage;
    recheck current facts after acquiring custody, never from the routing hint.
    """
    counts = {"suppressed": 0, "uncertain": 0}
    if type(limit) is not int or not 1 <= limit <= 100:
        return {**counts, "status": "invalid"}
    try:
        with global_session() as session:
            now = _database_now(session)
            latest_credential = (
                select(func.max(VisitPrepCredentialGeneration.generation))
                .where(
                    VisitPrepCredentialGeneration.visit_prep_id
                    == VisitPrepEmailDelivery.visit_prep_id
                )
                .correlate(VisitPrepEmailDelivery)
                .scalar_subquery()
            )
            latest_schedule = (
                select(func.max(VisitPrepScheduleGeneration.generation))
                .where(
                    VisitPrepScheduleGeneration.visit_prep_id
                    == VisitPrepEmailDelivery.visit_prep_id
                )
                .correlate(VisitPrepEmailDelivery)
                .scalar_subquery()
            )
            latest_consent_kind = (
                select(VisitPrepEmailConsentEvent.event_kind)
                .where(
                    VisitPrepEmailConsentEvent.visit_prep_id
                    == VisitPrepEmailDelivery.visit_prep_id
                )
                .order_by(col(VisitPrepEmailConsentEvent.event_sequence).desc())
                .limit(1)
                .correlate(VisitPrepEmailDelivery)
                .scalar_subquery()
            )
            latest_approval_kind = (
                select(VisitPrepEmailApprovalEvent.event_kind)
                .where(
                    VisitPrepEmailApprovalEvent.owner_practice_member_id
                    == VisitPrepEmailDelivery.owner_practice_member_id
                )
                .order_by(col(VisitPrepEmailApprovalEvent.event_sequence).desc())
                .limit(1)
                .correlate(VisitPrepEmailDelivery)
                .scalar_subquery()
            )
            identifiers = session.exec(
                select(col(VisitPrepEmailDelivery.id))
                .join(
                    VisitPrep,
                    col(VisitPrep.id) == col(VisitPrepEmailDelivery.visit_prep_id),
                )
                .join(
                    VisitPrepScheduleGeneration,
                    col(VisitPrepScheduleGeneration.id)
                    == col(VisitPrepEmailDelivery.schedule_generation_id),
                )
                .join(
                    VisitPrepCredentialGeneration,
                    and_(
                        VisitPrepCredentialGeneration.visit_prep_id
                        == VisitPrepEmailDelivery.visit_prep_id,
                        VisitPrepCredentialGeneration.generation
                        == VisitPrepEmailDelivery.credential_generation,
                    ),
                )
                .where(
                    or_(
                        and_(
                            VisitPrepEmailDelivery.state == "submitting",
                            or_(
                                VisitPrepEmailDelivery.expires_at <= now,
                                col(VisitPrepEmailDelivery.lease_until) <= now,
                            ),
                        ),
                        and_(
                            VisitPrepEmailDelivery.state == "queued",
                            or_(
                                VisitPrepEmailDelivery.expires_at <= now,
                                col(VisitPrep.revoked_at).is_not(None),
                                col(VisitPrep.misdirected_at).is_not(None),
                                col(VisitPrep.completed_at).is_not(None),
                                VisitPrepEmailDelivery.credential_generation
                                != latest_credential,
                                VisitPrepScheduleGeneration.generation
                                != latest_schedule,
                                VisitPrepScheduleGeneration.scheduled_at <= now,
                                col(VisitPrepCredentialGeneration.revoked_at).is_not(
                                    None
                                ),
                                VisitPrepCredentialGeneration.expires_at <= now,
                                latest_consent_kind == "withdrawn",
                                latest_approval_kind == "withdrawn",
                            ),
                        ),
                    )
                )
                .order_by(
                    col(VisitPrepEmailDelivery.requested_at),
                    col(VisitPrepEmailDelivery.id),
                )
                .limit(limit)
            ).all()
            for identifier in identifiers:
                pair = _lock_rows(session, identifier)
                if pair is None:
                    session.rollback()
                    continue
                prep, row = pair
                now = _database_now(session)
                if row.state == "submitting":
                    if row.lease_until is None or now >= min(
                        row.expires_at, row.lease_until
                    ):
                        _finish(
                            session,
                            prep,
                            row,
                            state="uncertain",
                            code="submission_uncertain",
                        )
                        counts["uncertain"] += 1
                    else:
                        session.rollback()
                    continue
                if row.state != "queued":
                    session.rollback()
                    continue
                code = None
                if now >= row.expires_at:
                    code = "expired"
                elif (
                    prep.revoked_at is not None
                    or prep.misdirected_at is not None
                    or prep.completed_at is not None
                ):
                    code = "obsolete_context"
                else:
                    schedule, credential = _lock_current_lineage(
                        session, visit_prep=prep
                    )
                    now = _database_now(session)
                    if (
                        row.schedule_generation_id != schedule.id
                        or row.credential_generation != credential.generation
                        or credential.revoked_at is not None
                        or credential.expires_at <= now
                        or schedule.scheduled_at <= now
                    ):
                        code = "obsolete_context"
                    else:
                        consent = session.exec(
                            select(VisitPrepEmailConsentEvent)
                            .where(VisitPrepEmailConsentEvent.visit_prep_id == prep.id)
                            .order_by(
                                cast(
                                    Any, VisitPrepEmailConsentEvent.event_sequence
                                ).desc()
                            )
                            .limit(1)
                        ).one_or_none()
                        if consent is not None and consent.event_kind == "withdrawn":
                            code = "consent_withdrawn"
                        else:
                            approval = session.exec(
                                select(VisitPrepEmailApprovalEvent)
                                .where(
                                    VisitPrepEmailApprovalEvent.owner_practice_member_id
                                    == prep.owner_practice_member_id
                                )
                                .order_by(
                                    col(
                                        VisitPrepEmailApprovalEvent.event_sequence
                                    ).desc()
                                )
                                .limit(1)
                            ).one_or_none()
                            if (
                                approval is not None
                                and approval.event_kind == "withdrawn"
                            ):
                                code = "scope_unavailable"
                if code is not None:
                    _finish(session, prep, row, state="suppressed", code=code)
                    counts["suppressed"] += 1
                else:
                    session.rollback()
            return {**counts, "status": "ok"}
    except Exception:
        return {**counts, "status": "storage_unavailable"}
