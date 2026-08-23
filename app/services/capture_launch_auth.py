"""Opaque desktop launch tickets and authorization-code/PKCE exchange.

The dashboard and system browser authenticate a real clinician through the
normal Clerk boundary. The desktop client receives only random or keyed-opaque
bearers plus the provider-neutral grant consumed by its Tauri core process.
No patient data, meeting data, or provider credential enters this protocol.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator, Literal, Protocol, cast
from uuid import UUID

from app.configuration.desktop_capture import DesktopCaptureSettings
from app.utils.encryption import pseudonymous_digest

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.capture_coordination import (
    CaptureLaunchAuthorization,
    CaptureSession,
    CaptureSourceLease,
)
from app.models.doctor import Doctor, DoctorRole
from app.models.practice import Practice, PracticeMember
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

DESKTOP_SOURCE = "desktop_companion"
PKCE_METHOD = "S256"
STOP_TOKEN_GRACE = timedelta(minutes=15)

_OPAQUE_SECRET = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_PKCE_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_PKCE_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_INSTALLATION_THUMBPRINT = re.compile(r"^[A-Za-z0-9_-]{43}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{8,255}$")


class CaptureLaunchError(RuntimeError):
    """A bounded launch failure whose message is safe for callers."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CaptureLaunchDisabled(CaptureLaunchError):
    pass


class CaptureLaunchUnavailable(CaptureLaunchError):
    pass


class CaptureLaunchDenied(CaptureLaunchError):
    pass


class CaptureLaunchConflict(CaptureLaunchError):
    pass


@dataclass(frozen=True)
class AuthorizedCaptureSessionReference:
    """Server-derived result of consent/session/source coordination."""

    capture_session_id: TypeIDType
    practice_id: TypeIDType
    actor_id: TypeIDType
    appointment_id: TypeIDType
    consent_evidence_id: UUID
    source_lease_id: TypeIDType
    state_version: int


@dataclass(frozen=True)
class CaptureConsentAttestation:
    """Bounded browser assertion; all identity and scope remain server-derived."""

    modality: Literal["in_person", "virtual", "phone"]
    participant_regions: frozenset[str]
    method: Literal["pre_visit_form", "verbal_attestation", "phone_ivr"]
    all_parties_consented: bool
    granted_at: datetime


class PrepareAuthorizedCaptureSession(Protocol):
    async def __call__(
        self,
        *,
        actor_id: TypeIDType,
        practice_id: TypeIDType,
        appointment_id: TypeIDType,
        idempotency_key: str,
        attestation: CaptureConsentAttestation,
    ) -> AuthorizedCaptureSessionReference: ...


async def disabled_capture_session_preparer(
    *,
    actor_id: TypeIDType,
    practice_id: TypeIDType,
    appointment_id: TypeIDType,
    idempotency_key: str,
    attestation: CaptureConsentAttestation,
) -> AuthorizedCaptureSessionReference:
    del actor_id, practice_id, appointment_id, idempotency_key, attestation
    raise CaptureLaunchUnavailable("capture_session_preparation_unavailable")


async def get_capture_session_preparer() -> AsyncIterator[
    PrepareAuthorizedCaptureSession
]:
    """Return the server-side consent/session coordinator.

    The default remains fail-closed. A fully configured, explicitly enabled
    Scribe workflow gets a request-scoped client that is closed even when the
    route fails after preparation. Tests may override this FastAPI dependency
    with a synthetic coordinator.
    """

    # Keep this import local so the Scribe adapter can depend on the launch
    # protocol without creating an import cycle.
    from app.services.scribe_capture_preparer import (
        DisabledScribeCaptureSessionPreparer,
        ScribeCaptureSessionPreparer,
        build_scribe_capture_session_preparer,
    )

    preparer = build_scribe_capture_session_preparer()
    try:
        yield preparer
    finally:
        if isinstance(preparer, ScribeCaptureSessionPreparer):
            await preparer.close()
        elif isinstance(preparer, DisabledScribeCaptureSessionPreparer):
            await preparer.close()


@dataclass(frozen=True)
class IssuedLaunchTicket:
    launch_ticket: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedAuthorizationCode:
    authorization_code: str
    expires_at: datetime
    redirect_uri: str


@dataclass(frozen=True)
class BackendCaptureGrant:
    capture_session_id: str
    actor_id: str
    consent_record_id: str
    consent_status: str
    session_status: str
    source: str
    source_lease_id: str


@dataclass(frozen=True)
class ResolvedDesktopAuthority:
    """Typed server authority plus the separately serialized wire grant."""

    practice_id: TypeIDType
    capture_session_id: TypeIDType
    actor_id: TypeIDType
    consent_evidence_id: UUID
    source_lease_id: TypeIDType
    grant: BackendCaptureGrant


@dataclass(frozen=True)
class ResolvedDesktopStopAuthority:
    """Exact historical bearer scope authorized only to move toward stop."""

    practice_id: TypeIDType
    capture_session_id: TypeIDType
    actor_id: TypeIDType
    consent_evidence_id: UUID
    source_lease_id: TypeIDType


@dataclass(frozen=True)
class ExchangedDesktopToken:
    access_token: str
    expires_at: datetime
    grant: BackendCaptureGrant


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("capture launch clock must be timezone-aware")
    return current.astimezone(UTC)


def _digest(value: str, *, context: str) -> str:
    return pseudonymous_digest(value, context=context)


def _ticket_digest(value: str) -> str:
    return _digest(value, context="desktop-launch-ticket-v1")


def _code_digest(value: str) -> str:
    return _digest(value, context="desktop-authorization-code-v1")


def _token_digest(value: str) -> str:
    return _digest(value, context="desktop-access-token-v1")


def _actor_digest(value: str) -> str:
    return _digest(value, context="desktop-actor-binding-v1")


def _redirect_digest(value: str) -> str:
    return _digest(value, context="desktop-redirect-uri-v1")


def _installation_digest(value: str) -> str:
    return _digest(value, context="desktop-installation-thumbprint-v1")


def require_desktop_capture_enabled(settings: DesktopCaptureSettings) -> None:
    if not settings.launch_is_configured():
        raise CaptureLaunchDisabled("desktop_capture_disabled")


def _validate_idempotency_key(value: str) -> str:
    if _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise CaptureLaunchDenied("launch_not_authorized")
    return value


def _validate_opaque_secret(value: str, *, code: str) -> str:
    if _OPAQUE_SECRET.fullmatch(value) is None:
        raise CaptureLaunchDenied(code)
    return value


def _validate_redirect_uri(value: str, settings: DesktopCaptureSettings) -> str:
    if value not in settings.redirect_uris:
        raise CaptureLaunchDenied("launch_not_authorized")
    return value


def _validate_pkce_challenge(value: str, method: str) -> str:
    if method != PKCE_METHOD or _PKCE_CHALLENGE.fullmatch(value) is None:
        raise CaptureLaunchDenied("launch_not_authorized")
    return value


def _validate_installation_thumbprint(value: str) -> str:
    if _INSTALLATION_THUMBPRINT.fullmatch(value) is None:
        raise CaptureLaunchDenied("launch_not_authorized")
    return value


def _pkce_challenge(verifier: str) -> str:
    if _PKCE_VERIFIER.fullmatch(verifier) is None:
        raise CaptureLaunchDenied("invalid_grant")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _constant_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


def _opaque_ticket(
    *,
    actor_binding: str,
    reference: AuthorizedCaptureSessionReference,
    idempotency_key: str,
) -> str:
    # A keyed deterministic value lets an identical HTTP retry return the
    # original bearer without storing the bearer itself. Domain separation in
    # ``pseudonymous_digest`` prevents reuse as any other credential.
    material = "\x00".join(
        (
            actor_binding,
            str(reference.practice_id),
            str(reference.actor_id),
            str(reference.appointment_id),
            str(reference.capture_session_id),
            str(reference.source_lease_id),
            idempotency_key,
        )
    )
    return _digest(material, context="desktop-launch-ticket-secret-v1")


def _mark_revoked(
    session: Session,
    authorization: CaptureLaunchAuthorization,
    now: datetime,
) -> None:
    if authorization.state == "ticket_issued":
        authorization.ticket_revoked_at = now
    elif authorization.state == "code_issued":
        authorization.authorization_code_revoked_at = now
    elif authorization.state == "token_issued":
        authorization.desktop_token_revoked_at = now
    authorization.state = "revoked"
    authorization.state_version += 1
    session.add(authorization)
    session.commit()


def _mark_expired(
    session: Session,
    authorization: CaptureLaunchAuthorization,
) -> None:
    authorization.state = "expired"
    authorization.state_version += 1
    session.add(authorization)
    session.commit()


def _authorized_scope(
    session: Session,
    authorization: CaptureLaunchAuthorization,
    now: datetime,
    *,
    allowed_session_states: tuple[str, ...] = ("ready",),
    allowed_lease_states: tuple[str, ...] = ("active",),
) -> tuple[CaptureSession, CaptureSourceLease]:
    doctor = session.exec(
        select(Doctor)
        .where(
            Doctor.id == authorization.actor_id,
            Doctor.role == DoctorRole.normal,
            cast(Any, Doctor.deleted_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    membership = session.exec(
        select(PracticeMember)
        .where(
            PracticeMember.practice_id == authorization.practice_id,
            PracticeMember.doctor_id == authorization.actor_id,
            cast(Any, PracticeMember.attested_at).is_not(None),
        )
        .with_for_update()
    ).one_or_none()
    practice = session.exec(
        select(Practice)
        .where(Practice.id == authorization.practice_id)
        .with_for_update()
    ).one_or_none()
    appointment = session.exec(
        select(Appointment)
        .where(
            Appointment.id == authorization.appointment_id,
            Appointment.doctor_id == authorization.actor_id,
            cast(Any, Appointment.cancelled_at).is_(None),
        )
        .with_for_update()
    ).one_or_none()
    capture_session = session.exec(
        select(CaptureSession)
        .where(
            CaptureSession.id == authorization.session_id,
            CaptureSession.practice_id == authorization.practice_id,
            CaptureSession.actor_id == authorization.actor_id,
            CaptureSession.appointment_id == authorization.appointment_id,
            CaptureSession.source_kind == DESKTOP_SOURCE,
            cast(Any, CaptureSession.state).in_(allowed_session_states),
            CaptureSession.consent_state == "granted",
            cast(Any, CaptureSession.scribe_consent_receipt_id).is_not(None),
        )
        .with_for_update()
    ).one_or_none()
    source_lease = session.exec(
        select(CaptureSourceLease)
        .where(
            CaptureSourceLease.id == authorization.source_lease_id,
            CaptureSourceLease.session_id == authorization.session_id,
            CaptureSourceLease.practice_id == authorization.practice_id,
            CaptureSourceLease.actor_id == authorization.actor_id,
            CaptureSourceLease.appointment_id == authorization.appointment_id,
            CaptureSourceLease.source_kind == DESKTOP_SOURCE,
            cast(Any, CaptureSourceLease.state).in_(allowed_lease_states),
            cast(Any, CaptureSourceLease.closed_at).is_(None),
            CaptureSourceLease.control_deadline_at > now,
        )
        .with_for_update()
    ).one_or_none()
    if any(
        value is None
        for value in (
            doctor,
            membership,
            practice,
            appointment,
            capture_session,
            source_lease,
        )
    ):
        raise CaptureLaunchDenied("launch_not_authorized")
    assert practice is not None
    if not practice.baa_in_force() or not practice.entitled():
        raise CaptureLaunchDenied("launch_not_authorized")
    assert capture_session is not None
    assert source_lease is not None
    return capture_session, source_lease


def issue_launch_ticket(
    *,
    reference: AuthorizedCaptureSessionReference,
    actor_binding: str,
    idempotency_key: str,
    settings: DesktopCaptureSettings,
    now: datetime | None = None,
) -> IssuedLaunchTicket:
    """Issue or replay an opaque two-minute dashboard launch ticket."""

    require_desktop_capture_enabled(settings)
    issued_at = _now(now)
    idempotency_key = _validate_idempotency_key(idempotency_key)
    ticket = _opaque_ticket(
        actor_binding=actor_binding,
        reference=reference,
        idempotency_key=idempotency_key,
    )
    digest = _ticket_digest(ticket)

    with get_session() as session:
        provisional = CaptureLaunchAuthorization(
            session_id=reference.capture_session_id,
            practice_id=reference.practice_id,
            actor_id=reference.actor_id,
            appointment_id=reference.appointment_id,
            source_lease_id=reference.source_lease_id,
            actor_binding_digest=_actor_digest(actor_binding),
            installation_thumbprint_digest=None,
            launch_ticket_digest=digest,
            ticket_expires_at=issued_at
            + timedelta(seconds=settings.launch_ticket_ttl_seconds),
        )
        # SQLModel's generated constructor typing omits inherited timestamp
        # mixin fields even though they are mapped attributes. Set them after
        # construction so the DB boundary uses the same trusted clock as the
        # ticket lifetime without suppressing type checking.
        provisional.created_at = issued_at
        provisional.updated_at = issued_at
        try:
            session.add(provisional)
            session.flush()
            capture_session, source_lease = _authorized_scope(
                session, provisional, issued_at
            )
            if (
                capture_session.scribe_consent_receipt_id
                != reference.consent_evidence_id
                or source_lease.id != reference.source_lease_id
                or capture_session.state_version != reference.state_version
            ):
                raise CaptureLaunchDenied("launch_not_authorized")
            session.commit()
            session.refresh(provisional)
            return IssuedLaunchTicket(ticket, provisional.ticket_expires_at)
        except IntegrityError:
            session.rollback()
            existing = session.exec(
                select(CaptureLaunchAuthorization)
                .where(CaptureLaunchAuthorization.launch_ticket_digest == digest)
                .with_for_update()
            ).one_or_none()
            if existing is None:
                raise CaptureLaunchConflict("launch_conflict") from None
            if (
                existing.session_id != reference.capture_session_id
                or existing.practice_id != reference.practice_id
                or existing.actor_id != reference.actor_id
                or existing.appointment_id != reference.appointment_id
                or existing.source_lease_id != reference.source_lease_id
                or not _constant_equal(
                    existing.actor_binding_digest, _actor_digest(actor_binding)
                )
            ):
                raise CaptureLaunchConflict("launch_conflict") from None
            if (
                existing.state != "ticket_issued"
                or existing.ticket_consumed_at is not None
                or existing.ticket_revoked_at is not None
                or existing.ticket_expires_at <= issued_at
            ):
                raise CaptureLaunchConflict("launch_conflict") from None
            try:
                _authorized_scope(session, existing, issued_at)
            except CaptureLaunchDenied:
                _mark_revoked(session, existing, issued_at)
                raise CaptureLaunchConflict("launch_conflict") from None
            return IssuedLaunchTicket(ticket, existing.ticket_expires_at)
        except SQLAlchemyError as exc:
            session.rollback()
            raise CaptureLaunchUnavailable("capture_launch_unavailable") from exc


def authorize_launch_ticket(
    *,
    launch_ticket: str,
    actor_id: TypeIDType,
    practice_id: TypeIDType,
    actor_binding: str,
    code_challenge: str,
    code_challenge_method: str,
    redirect_uri: str,
    installation_thumbprint: str,
    settings: DesktopCaptureSettings,
    now: datetime | None = None,
) -> IssuedAuthorizationCode:
    """Consume a ticket in the authenticated system browser and issue one code."""

    require_desktop_capture_enabled(settings)
    current = _now(now)
    ticket = _validate_opaque_secret(launch_ticket, code="launch_not_authorized")
    challenge = _validate_pkce_challenge(code_challenge, code_challenge_method)
    redirect = _validate_redirect_uri(redirect_uri, settings)
    installation = _validate_installation_thumbprint(installation_thumbprint)

    with get_session() as session:
        try:
            authorization = session.exec(
                select(CaptureLaunchAuthorization)
                .where(
                    CaptureLaunchAuthorization.launch_ticket_digest
                    == _ticket_digest(ticket)
                )
                .with_for_update()
            ).one_or_none()
            if authorization is None:
                raise CaptureLaunchDenied("launch_not_authorized")
            if (
                authorization.state != "ticket_issued"
                or authorization.ticket_consumed_at is not None
                or authorization.ticket_revoked_at is not None
                or authorization.ticket_expires_at <= current
            ):
                if (
                    authorization.state == "ticket_issued"
                    and authorization.ticket_expires_at <= current
                ):
                    _mark_expired(session, authorization)
                raise CaptureLaunchDenied("launch_not_authorized")
            if (
                authorization.actor_id != actor_id
                or authorization.practice_id != practice_id
                or not _constant_equal(
                    authorization.actor_binding_digest,
                    _actor_digest(actor_binding),
                )
            ):
                raise CaptureLaunchDenied("launch_not_authorized")
            try:
                _authorized_scope(session, authorization, current)
            except CaptureLaunchDenied:
                _mark_revoked(session, authorization, current)
                raise

            authorization_code = secrets.token_urlsafe(32)
            expires_at = current + timedelta(
                seconds=settings.authorization_code_ttl_seconds
            )
            authorization.ticket_consumed_at = current
            authorization.redirect_uri_digest = _redirect_digest(redirect)
            authorization.pkce_code_challenge = challenge
            authorization.pkce_method = PKCE_METHOD
            authorization.installation_thumbprint_digest = _installation_digest(
                installation
            )
            authorization.authorization_code_digest = _code_digest(authorization_code)
            authorization.authorization_code_issued_at = current
            authorization.authorization_code_expires_at = expires_at
            authorization.state = "code_issued"
            authorization.state_version += 1
            session.add(authorization)
            session.commit()
            return IssuedAuthorizationCode(
                authorization_code=authorization_code,
                expires_at=expires_at,
                redirect_uri=redirect,
            )
        except CaptureLaunchError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise CaptureLaunchUnavailable("capture_launch_unavailable") from exc


def _grant(
    capture_session: CaptureSession,
    source_lease: CaptureSourceLease,
) -> BackendCaptureGrant:
    consent_record_id = capture_session.scribe_consent_receipt_id
    if consent_record_id is None:
        raise CaptureLaunchDenied("invalid_grant")
    return BackendCaptureGrant(
        capture_session_id=str(capture_session.id),
        actor_id=str(capture_session.actor_id),
        consent_record_id=str(consent_record_id),
        consent_status="granted",
        session_status="authorized",
        source=DESKTOP_SOURCE,
        source_lease_id=str(source_lease.id),
    )


def exchange_authorization_code(
    *,
    authorization_code: str,
    code_verifier: str,
    redirect_uri: str,
    settings: DesktopCaptureSettings,
    now: datetime | None = None,
) -> ExchangedDesktopToken:
    """Atomically consume one authorization code after an RFC 7636 S256 proof."""

    require_desktop_capture_enabled(settings)
    current = _now(now)
    code = _validate_opaque_secret(authorization_code, code="invalid_grant")
    expected_challenge = _pkce_challenge(code_verifier)
    redirect = _validate_redirect_uri(redirect_uri, settings)

    with get_session() as session:
        try:
            authorization = session.exec(
                select(CaptureLaunchAuthorization)
                .where(
                    CaptureLaunchAuthorization.authorization_code_digest
                    == _code_digest(code)
                )
                .with_for_update()
            ).one_or_none()
            if authorization is None:
                raise CaptureLaunchDenied("invalid_grant")
            if (
                authorization.state != "code_issued"
                or authorization.authorization_code_consumed_at is not None
                or authorization.authorization_code_revoked_at is not None
                or authorization.authorization_code_expires_at is None
                or authorization.authorization_code_expires_at <= current
            ):
                if (
                    authorization.state == "code_issued"
                    and authorization.authorization_code_expires_at is not None
                    and authorization.authorization_code_expires_at <= current
                ):
                    _mark_expired(session, authorization)
                raise CaptureLaunchDenied("invalid_grant")
            if (
                authorization.pkce_method != PKCE_METHOD
                or authorization.pkce_code_challenge is None
                or not _constant_equal(
                    authorization.pkce_code_challenge, expected_challenge
                )
                or authorization.redirect_uri_digest is None
                or not _constant_equal(
                    authorization.redirect_uri_digest,
                    _redirect_digest(redirect),
                )
            ):
                raise CaptureLaunchDenied("invalid_grant")
            try:
                capture_session, source_lease = _authorized_scope(
                    session, authorization, current
                )
            except CaptureLaunchDenied:
                _mark_revoked(session, authorization, current)
                raise CaptureLaunchDenied("invalid_grant") from None

            access_token = secrets.token_urlsafe(32)
            expires_at = current + timedelta(seconds=settings.desktop_token_ttl_seconds)
            authorization.authorization_code_consumed_at = current
            authorization.desktop_token_digest = _token_digest(access_token)
            authorization.desktop_token_issued_at = current
            authorization.desktop_token_expires_at = expires_at
            authorization.state = "token_issued"
            authorization.state_version += 1
            session.add(authorization)
            session.commit()
            return ExchangedDesktopToken(
                access_token=access_token,
                expires_at=expires_at,
                grant=_grant(capture_session, source_lease),
            )
        except CaptureLaunchError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise CaptureLaunchUnavailable("capture_launch_unavailable") from exc


def _resolve_desktop_authority(
    *,
    access_token: str,
    settings: DesktopCaptureSettings,
    allowed_session_states: tuple[str, ...],
    allowed_lease_states: tuple[str, ...],
    now: datetime | None = None,
) -> ResolvedDesktopAuthority:
    require_desktop_capture_enabled(settings)
    current = _now(now)
    token = _validate_opaque_secret(access_token, code="invalid_token")
    with get_session() as session:
        try:
            authorization = session.exec(
                select(CaptureLaunchAuthorization)
                .where(
                    CaptureLaunchAuthorization.desktop_token_digest
                    == _token_digest(token)
                )
                .with_for_update()
            ).one_or_none()
            if (
                authorization is None
                or authorization.state != "token_issued"
                or authorization.desktop_token_revoked_at is not None
                or authorization.desktop_token_expires_at is None
                or authorization.desktop_token_expires_at <= current
            ):
                if (
                    authorization is not None
                    and authorization.state == "token_issued"
                    and authorization.desktop_token_expires_at is not None
                    and authorization.desktop_token_expires_at <= current
                ):
                    _mark_expired(session, authorization)
                raise CaptureLaunchDenied("invalid_token")
            try:
                capture_session, source_lease = _authorized_scope(
                    session,
                    authorization,
                    current,
                    allowed_session_states=allowed_session_states,
                    allowed_lease_states=allowed_lease_states,
                )
            except CaptureLaunchDenied:
                _mark_revoked(session, authorization, current)
                raise CaptureLaunchDenied("invalid_token") from None
            consent_evidence_id = capture_session.scribe_consent_receipt_id
            if consent_evidence_id is None:
                raise CaptureLaunchDenied("invalid_token")
            return ResolvedDesktopAuthority(
                practice_id=authorization.practice_id,
                capture_session_id=capture_session.id,
                actor_id=capture_session.actor_id,
                consent_evidence_id=consent_evidence_id,
                source_lease_id=source_lease.id,
                grant=_grant(capture_session, source_lease),
            )
        except CaptureLaunchError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise CaptureLaunchUnavailable("capture_launch_unavailable") from exc


def resolve_desktop_authority(
    *,
    access_token: str,
    settings: DesktopCaptureSettings,
    now: datetime | None = None,
) -> ResolvedDesktopAuthority:
    """Resolve start authority and retain its server-only practice binding."""

    return _resolve_desktop_authority(
        access_token=access_token,
        settings=settings,
        allowed_session_states=("ready", "starting"),
        allowed_lease_states=("active",),
        now=now,
    )


def resolve_desktop_control_authority(
    *,
    access_token: str,
    settings: DesktopCaptureSettings,
    now: datetime | None = None,
) -> ResolvedDesktopAuthority:
    """Resolve heartbeat authority within the configured bearer lifetime."""

    return _resolve_desktop_authority(
        access_token=access_token,
        settings=settings,
        allowed_session_states=("starting", "recording", "stopping"),
        allowed_lease_states=("active", "closing"),
        now=now,
    )


def resolve_desktop_stop_authority(
    *,
    access_token: str,
    now: datetime | None = None,
) -> ResolvedDesktopStopAuthority:
    """Resolve an exact bearer for the destructive-only stop safety path.

    Stop never starts, refreshes, or authorizes capture.  An already-issued
    bearer therefore remains usable for this one narrowing action after
    expiry/revocation or live-clinician authority loss, provided its exact
    session and source lease are still open and nonterminal.
    """

    current = _now(now)
    token = _validate_opaque_secret(access_token, code="invalid_token")
    with get_session() as session:
        try:
            authorization = session.exec(
                select(CaptureLaunchAuthorization)
                .where(
                    CaptureLaunchAuthorization.desktop_token_digest
                    == _token_digest(token)
                )
                .with_for_update()
            ).one_or_none()
            if (
                authorization is None
                or authorization.state not in {"token_issued", "expired", "revoked"}
                or authorization.desktop_token_issued_at is None
                or authorization.desktop_token_expires_at is None
                or authorization.desktop_token_expires_at + STOP_TOKEN_GRACE <= current
            ):
                raise CaptureLaunchDenied("invalid_token")
            capture_session = session.exec(
                select(CaptureSession)
                .where(
                    CaptureSession.id == authorization.session_id,
                    CaptureSession.practice_id == authorization.practice_id,
                    CaptureSession.actor_id == authorization.actor_id,
                    CaptureSession.appointment_id == authorization.appointment_id,
                    CaptureSession.source_kind == DESKTOP_SOURCE,
                    cast(Any, CaptureSession.state).in_(
                        (
                            "starting",
                            "recording",
                            "stopping",
                            "paused",
                            "review_required",
                            "failed",
                        )
                    ),
                    cast(Any, CaptureSession.ended_at).is_(None),
                    cast(Any, CaptureSession.scribe_consent_receipt_id).is_not(None),
                    CaptureSession.authority_origin == "server_authorized",
                )
                .with_for_update()
            ).one_or_none()
            source_lease = session.exec(
                select(CaptureSourceLease)
                .where(
                    CaptureSourceLease.id == authorization.source_lease_id,
                    CaptureSourceLease.session_id == authorization.session_id,
                    CaptureSourceLease.practice_id == authorization.practice_id,
                    CaptureSourceLease.actor_id == authorization.actor_id,
                    CaptureSourceLease.appointment_id == authorization.appointment_id,
                    CaptureSourceLease.source_kind == DESKTOP_SOURCE,
                    cast(Any, CaptureSourceLease.state).in_(
                        ("active", "uncertain", "closing")
                    ),
                    cast(Any, CaptureSourceLease.closed_at).is_(None),
                )
                .with_for_update()
            ).one_or_none()
            if capture_session is None or source_lease is None:
                raise CaptureLaunchDenied("invalid_token")
            consent_evidence_id = capture_session.scribe_consent_receipt_id
            if consent_evidence_id is None:
                raise CaptureLaunchDenied("invalid_token")
            return ResolvedDesktopStopAuthority(
                practice_id=authorization.practice_id,
                capture_session_id=capture_session.id,
                actor_id=capture_session.actor_id,
                consent_evidence_id=consent_evidence_id,
                source_lease_id=source_lease.id,
            )
        except CaptureLaunchError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise CaptureLaunchUnavailable("capture_launch_unavailable") from exc


def resolve_desktop_token(
    *,
    access_token: str,
    settings: DesktopCaptureSettings,
    now: datetime | None = None,
) -> BackendCaptureGrant:
    """Resolve a short-lived main-process token against current server authority."""

    return resolve_desktop_authority(
        access_token=access_token,
        settings=settings,
        now=now,
    ).grant
