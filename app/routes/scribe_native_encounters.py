"""Default-off human and singleton-workload native visit facade versions."""

import asyncio
import json
from contextvars import Context
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import Response

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.configuration.scribe_native import (
    native_authority_settings,
    native_encounter_settings,
    native_existing_patient_visits_enabled,
    native_visit_actions_v2_enabled,
)
from app.routes.companion_records import RecordBoundary, problem
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.dependencies.view_as import view_as_browser_session_present
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
    get_authority_kernel_settings,
)
from app.routes.scribe_encounters import read_body
from app.routes.scribe_native_capture import authenticate_native_workload
from app.services import authority, companion_records, scribe_encounters
from app.services.provider_record_authority import ProviderRecordScopeDenied
from app.services.scribe_encounter_contract import (
    CreateRequest,
    ExistingPatient,
    PatientMatchCandidatesRequest,
    PatientMatchSaveRequest,
    PatientsRequest,
    PrivateNoteReadRequest,
    PrivateNoteSaveRequest,
)
from app.services.scribe_native_encounters import execute_native_encounter
from app.services.superadmin_capability import SuperadminCapabilityError
from app.services.view_as import ViewAsError
from app.utils.encryption import DecryptionError, EncryptionError

from sqlalchemy.exc import SQLAlchemyError

PATH = "/service/v1/agent/scribe/native-encounters"
router = APIRouter()


@router.post(PATH, response_model=None, include_in_schema=False)
async def native_encounters(
    request: Request,
    principal: Annotated[EntraServicePrincipal, Depends(authenticate_native_workload)],
    identity: Annotated[
        VerifiedClerkIdentity, Depends(authenticate_embed_platform_session)
    ],
    enabled: Annotated[bool, Depends(native_existing_patient_visits_enabled)],
    auth: Annotated[AuthorityKernelSettings, Depends(get_authority_kernel_settings)],
) -> Response:
    deadline = getattr(request.state, "companion_record_deadline", None)
    if not enabled or not isinstance(deadline, companion_records.RecordBudget):
        return problem(503)
    if (
        view_as_browser_session_present(request)
        or request.headers.getlist("cookie")
        or any(
            name in request.headers
            for name in (
                "x-cosmaneura-mount-context",
                "x-cosmaneura-interaction-ref",
                "x-cosmaneura-client-turn-id",
            )
        )
    ):
        return problem(403)
    versions = request.headers.getlist("x-cosmaneura-scribe-encounter-version")
    if versions not in (["1"], ["2"]):
        return problem(426)
    full_visit = versions == ["2"]
    if full_visit and not native_visit_actions_v2_enabled():
        return problem(503)
    try:
        data = await read_body(request)
        allowed = (
            (
                PatientsRequest,
                CreateRequest,
                PatientMatchCandidatesRequest,
                PatientMatchSaveRequest,
                PrivateNoteReadRequest,
                PrivateNoteSaveRequest,
            )
            if full_visit
            else (PatientsRequest, CreateRequest)
        )
        if not isinstance(data, allowed) or (
            not full_visit
            and isinstance(data, CreateRequest)
            and not isinstance(data.patient, ExistingPatient)
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return problem(422)
    try:
        result = await asyncio.to_thread(
            Context().run,
            execute_native_encounter,
            issuer=identity.issuer,
            subject=identity.subject,
            session_id=identity.clerk_session_id,
            request=data,
            authority_settings=native_authority_settings(auth),
            # This gate is re-read by the existing service before commit. It
            # never enables the browser encounter or global authority setting.
            settings_provider=lambda: ScribeEncounterSettings(
                enabled=(
                    enabled
                    and native_existing_patient_visits_enabled()
                    and native_encounter_settings().enabled
                    and (not full_visit or native_visit_actions_v2_enabled())
                )
            ),
            deadline=deadline,
            **({"native_visit_actions_v2": True} if full_visit else {}),
        )
        companion_records.check_deadline(deadline)
        raw = json.dumps(
            result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(raw) > 1_048_576:
            raise companion_records.RecordUnavailable
        return Response(
            raw,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "X-Cosmaneura-Scribe-Encounter-Version": "2" if full_visit else "1",
            },
        )
    except scribe_encounters.EncounterConflict:
        return problem(409)
    except companion_records.RecordDeadline:
        return problem(504)
    except authority.AuthorityKernelDisabledError:
        return problem(503)
    except (
        authority.AuthorityKernelError,
        ProviderRecordScopeDenied,
        SuperadminCapabilityError,
        ViewAsError,
    ):
        return problem(403)
    except ValueError:
        return problem(422)
    except (
        companion_records.RecordUnavailable,
        SQLAlchemyError,
        TypeError,
        EncryptionError,
        DecryptionError,
    ):
        return problem(503)


def install(app: FastAPI) -> None:
    app.include_router(router)
    app.add_middleware(RecordBoundary, path=PATH)
