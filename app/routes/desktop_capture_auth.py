"""Provider-neutral browser-to-desktop launch authorization routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app import log
from app.configuration.desktop_capture import (
    DesktopCaptureSettings,
    get_desktop_capture_settings,
)
from app.routes.dependencies.real_clinician import (
    RealClinicianContext,
    require_real_clinician,
)
from app.routes.desktop_capture_contract import desktop_error_responses
from app.services.capture_launch_auth import (
    CaptureConsentAttestation,
    CaptureLaunchConflict,
    CaptureLaunchDenied,
    CaptureLaunchDisabled,
    CaptureLaunchUnavailable,
    PrepareAuthorizedCaptureSession,
    authorize_launch_ticket,
    exchange_authorization_code,
    get_capture_session_preparer,
    issue_launch_ticket,
    require_desktop_capture_enabled,
)

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType

desktop_capture_browser_app = APIRouter(
    prefix="/desktop-capture",
    tags=["desktop-capture"],
)

desktop_capture_token_app = APIRouter(
    prefix="/desktop/v1",
    tags=["desktop-capture"],
    dependencies=[Depends(aglobal_session)],
)


class LaunchTicketResponse(BaseModel, extra="forbid"):
    launch_ticket: str
    expires_at: str


ParticipantRegion = Annotated[
    str,
    Field(min_length=2, max_length=5, pattern=r"^(?:[A-Z]{2}|US-[A-Z]{2})$"),
]


class CaptureConsentAttestationRequest(BaseModel, extra="forbid"):
    """No identity, encounter, tenant, or evidence IDs are accepted here."""

    modality: Literal["virtual"]
    participant_regions: set[ParticipantRegion] = Field(min_length=1, max_length=8)
    method: Literal["pre_visit_form", "verbal_attestation", "phone_ivr"]
    all_parties_consented: Annotated[bool, Field(strict=True)]
    granted_at: datetime

    def to_domain(self) -> CaptureConsentAttestation:
        return CaptureConsentAttestation(
            modality=self.modality,
            participant_regions=frozenset(self.participant_regions),
            method=self.method,
            all_parties_consented=self.all_parties_consented,
            granted_at=self.granted_at,
        )


class AuthorizeLaunchRequest(BaseModel, extra="forbid"):
    launch_ticket: str = Field(min_length=43, max_length=128)
    code_challenge: str = Field(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
    )
    code_challenge_method: Literal["S256"]
    redirect_uri: str = Field(min_length=1, max_length=512)
    installation_thumbprint: str = Field(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
    )


class AuthorizationCodeResponse(BaseModel, extra="forbid"):
    authorization_code: str
    expires_at: str
    redirect_uri: str


class AuthorizationCodeExchange(BaseModel, extra="forbid"):
    grant_type: Literal["authorization_code"]
    code: str = Field(min_length=43, max_length=128)
    code_verifier: str = Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]+$",
    )
    redirect_uri: str = Field(min_length=1, max_length=512)


class BackendCaptureGrantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    capture_session_id: str = Field(serialization_alias="captureSessionId")
    actor_id: str = Field(serialization_alias="actorId")
    consent_record_id: str = Field(serialization_alias="consentRecordId")
    consent_status: Literal["granted"] = Field(serialization_alias="consentStatus")
    session_status: Literal["authorized"] = Field(serialization_alias="sessionStatus")
    source: Literal["desktop_companion"]
    source_lease_id: str = Field(serialization_alias="sourceLeaseId")


class DesktopTokenResponse(BaseModel, extra="forbid"):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    grant: BackendCaptureGrantResponse


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Desktop capture is unavailable.",
    )


def _prevent_credential_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@desktop_capture_browser_app.post(
    "/appointments/{appointment_id}/launch",
    response_model=LaunchTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_desktop_capture_launch(
    appointment_id: Annotated[TypeIDType, Path()],
    data: CaptureConsentAttestationRequest,
    response: Response,
    clinician: Annotated[RealClinicianContext, Depends(require_real_clinician)],
    preparer: Annotated[
        PrepareAuthorizedCaptureSession, Depends(get_capture_session_preparer)
    ],
    settings: Annotated[DesktopCaptureSettings, Depends(get_desktop_capture_settings)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=255,
            pattern=r"^[A-Za-z0-9._~-]+$",
        ),
    ],
) -> LaunchTicketResponse:
    """Create one server-derived capture session and its short launch ticket."""

    try:
        # The kill switch is checked before the preparer so disabled requests
        # cannot reach Scribe, a capture provider, or any other external call.
        require_desktop_capture_enabled(settings)
        prepared = await preparer(
            actor_id=clinician.actor_id,
            practice_id=clinician.practice_id,
            appointment_id=appointment_id,
            idempotency_key=idempotency_key,
            attestation=data.to_domain(),
        )
        if (
            prepared.actor_id != clinician.actor_id
            or prepared.practice_id != clinician.practice_id
            or prepared.appointment_id != appointment_id
        ):
            raise CaptureLaunchDenied("launch_not_authorized")
        ticket = issue_launch_ticket(
            reference=prepared,
            actor_binding=clinician.actor_binding,
            idempotency_key=idempotency_key,
            settings=settings,
        )
    except CaptureLaunchDisabled as exc:
        raise _unavailable() from exc
    except CaptureLaunchUnavailable as exc:
        raise _unavailable() from exc
    except CaptureLaunchConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Desktop capture launch conflicts with current state.",
        ) from exc
    except CaptureLaunchDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment is unavailable for desktop capture.",
        ) from exc

    _prevent_credential_caching(response)
    log.info(
        "desktop capture launch issued",
        event_code="desktop_capture_launch_issued",
    )
    return LaunchTicketResponse(
        launch_ticket=ticket.launch_ticket,
        expires_at=ticket.expires_at.isoformat().replace("+00:00", "Z"),
    )


@desktop_capture_browser_app.post(
    "/authorize",
    response_model=AuthorizationCodeResponse,
)
def authorize_desktop_capture_launch(
    data: AuthorizeLaunchRequest,
    response: Response,
    clinician: Annotated[RealClinicianContext, Depends(require_real_clinician)],
    settings: Annotated[DesktopCaptureSettings, Depends(get_desktop_capture_settings)],
) -> AuthorizationCodeResponse:
    """Bind a launch ticket to the same clinician and desktop PKCE proof."""

    try:
        issued = authorize_launch_ticket(
            launch_ticket=data.launch_ticket,
            actor_id=clinician.actor_id,
            practice_id=clinician.practice_id,
            actor_binding=clinician.actor_binding,
            code_challenge=data.code_challenge,
            code_challenge_method=data.code_challenge_method,
            redirect_uri=data.redirect_uri,
            installation_thumbprint=data.installation_thumbprint,
            settings=settings,
        )
    except CaptureLaunchDisabled as exc:
        raise _unavailable() from exc
    except CaptureLaunchUnavailable as exc:
        raise _unavailable() from exc
    except CaptureLaunchDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desktop capture authorization was denied.",
        ) from exc

    _prevent_credential_caching(response)
    log.info(
        "desktop capture launch authorized",
        event_code="desktop_capture_launch_authorized",
    )
    return AuthorizationCodeResponse(
        authorization_code=issued.authorization_code,
        expires_at=issued.expires_at.isoformat().replace("+00:00", "Z"),
        redirect_uri=issued.redirect_uri,
    )


@desktop_capture_token_app.post(
    "/oauth/token",
    response_model=DesktopTokenResponse,
    responses=desktop_error_responses(400, 422, 503),
)
def exchange_desktop_capture_code(
    data: AuthorizationCodeExchange,
    response: Response,
    settings: Annotated[DesktopCaptureSettings, Depends(get_desktop_capture_settings)],
) -> DesktopTokenResponse:
    """Exchange one code plus its PKCE verifier for a main-process token/grant."""

    try:
        exchanged = exchange_authorization_code(
            authorization_code=data.code,
            code_verifier=data.code_verifier,
            redirect_uri=data.redirect_uri,
            settings=settings,
        )
    except CaptureLaunchDisabled as exc:
        raise _unavailable() from exc
    except CaptureLaunchUnavailable as exc:
        raise _unavailable() from exc
    except CaptureLaunchDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_grant",
        ) from exc

    _prevent_credential_caching(response)
    grant = exchanged.grant
    log.info(
        "desktop capture token issued",
        event_code="desktop_capture_token_issued",
    )
    return DesktopTokenResponse(
        access_token=exchanged.access_token,
        expires_in=settings.desktop_token_ttl_seconds,
        grant=BackendCaptureGrantResponse(
            capture_session_id=grant.capture_session_id,
            actor_id=grant.actor_id,
            consent_record_id=grant.consent_record_id,
            consent_status="granted",
            session_status="authorized",
            source="desktop_companion",
            source_lease_id=grant.source_lease_id,
        ),
    )
