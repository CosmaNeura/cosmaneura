"""Owner-only synthesis requests and atomic current source/brief reads.

Requests contain only the displayed source hash. The worker selects sources
from Core and records usage; this router never invokes a model, publishes
caller output, dispatches broker payloads or discloses retained stale output.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal, TypedDict, TypeVar, cast

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import Field
from starlette.responses import JSONResponse

from app.routes.dependencies.visit_prep import require_visit_prep_package_read
from app.routes.visit_prep_packages import (
    VisitPrepPatientSourcesResponse,
    _query_keys,
    _source_response,
)
from app.routes.visit_prep_preparations import (
    _COMMON_ERROR_RESPONSES,
    _invoke,
    _scope,
    _SensitiveVisitPrepRoute,
    _StrictModel,
    _type_id,
)
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_synthesis_builds import request_visit_prep_synthesis
from app.services.visit_prep_synthesis_contract import (
    GENERATOR_VERSION,
    PROMPT_VERSION,
    VisitPrepPreparedBrief,
    VisitPrepSynthesisInvalid,
)
from app.services.visit_prep_synthesis_generator import MODEL_PROFILE
from app.services.visit_prep_synthesis_publication import (
    VisitPrepSynthesisView,
    get_visit_prep_synthesis,
)
from app.services.visit_preps import VisitPrepUnavailable
from app.utils.encryption import EncryptionError

from activemodel.types import TypeIDType

_ResultT = TypeVar("_ResultT")


class _OwnerArgs(TypedDict):
    owner_practice_member_id: TypeIDType
    owner_doctor_id: TypeIDType
    practice_id: TypeIDType
    expected_clerk_subject: str
    visit_prep_id: TypeIDType


class _SensitiveSynthesisRoute(_SensitiveVisitPrepRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_handler(request: Request):
            if request.method == "POST":
                # Bound bytes before FastAPI allocates/validates arbitrary JSON.
                # Actual chunks are authoritative even with a missing/false
                # Content-Length. This scope changes no other provider route.
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 1024:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Visit Prep request is invalid."},
                            headers={
                                "Cache-Control": "no-store",
                                "Referrer-Policy": "no-referrer",
                            },
                        )
                    body.extend(chunk)
                request._body = bytes(body)  # noqa: SLF001
            return await handler(request)

        return bounded_handler


class VisitPrepSynthesisRequest(_StrictModel):
    expected_source_version: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)


class VisitPrepSynthesisRequestResponse(_StrictModel):
    package_id: str
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["pending", "running", "ready", "failed"]


class VisitPrepSynthesisResponse(_StrictModel):
    contract: Literal["visit_prep.patient_synthesis.v1"] = (
        "visit_prep.patient_synthesis.v1"
    )
    sources: VisitPrepPatientSourcesResponse = Field(repr=False)
    status: Literal["missing", "stale", "ready"]
    build_status: Literal["not_requested", "pending", "running", "failed", "ready"]
    retry_at: datetime | None
    artifact_id: str | None
    version: int | None
    recorded_at: datetime | None
    generator_version: str
    prompt_version: str
    model_profile: str
    brief: VisitPrepPreparedBrief | None = Field(repr=False)


visit_prep_synthesis_api_app = APIRouter(
    prefix="/visit-prep/preps",
    tags=["visit-prep"],
    route_class=_SensitiveSynthesisRoute,
    responses={
        **_COMMON_ERROR_RESPONSES,
        413: {"description": "The bounded request is too large."},
    },
)


def _invoke_synthesis(operation: Callable[[], _ResultT]) -> _ResultT:
    def protected() -> _ResultT:
        try:
            return operation()
        except (VisitPrepSynthesisInvalid, EncryptionError):
            # Corrupt/invalid retained output is unavailable, not a validation
            # error that exposes patient/model text or an exception traceback.
            raise VisitPrepUnavailable("Visit Prep is unavailable") from None

    return _invoke(protected)


def _owner_args(
    request: Request, decision: VisitPrepAccessDecision, prep_id: str
) -> _OwnerArgs:
    owner_id, doctor_id, practice_id = _scope(decision)
    return {
        "owner_practice_member_id": owner_id,
        "owner_doctor_id": doctor_id,
        "practice_id": practice_id,
        "expected_clerk_subject": request.state.doctor.clerk_id,
        "visit_prep_id": _type_id(prep_id, prefix="visit_prep", path=True),
    }


@visit_prep_synthesis_api_app.post(
    "/{prep_id}/synthesis",
    name="visit_prep_synthesis_request",
    response_model=VisitPrepSynthesisRequestResponse,
    status_code=202,
)
def visit_prep_synthesis_request(
    request: Request,
    response: Response,
    prep_id: Annotated[str, Path(max_length=80)],
    payload: VisitPrepSynthesisRequest,
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepSynthesisRequestResponse:
    _query_keys(request, frozenset())
    args = _owner_args(request, decision, prep_id)
    result = _invoke_synthesis(
        lambda: request_visit_prep_synthesis(
            **args, expected_source_version=payload.expected_source_version
        )
    )
    if result.state in {"ready", "failed"}:
        response.status_code = 200
    return VisitPrepSynthesisRequestResponse(
        package_id=prep_id,
        source_version=result.source_version,
        state=cast(Literal["pending", "running", "ready", "failed"], result.state),
    )


@visit_prep_synthesis_api_app.get(
    "/{prep_id}/synthesis",
    name="visit_prep_synthesis",
    response_model=VisitPrepSynthesisResponse,
)
def visit_prep_synthesis(
    request: Request,
    prep_id: Annotated[str, Path(max_length=80)],
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepSynthesisResponse:
    _query_keys(request, frozenset())
    args = _owner_args(request, decision, prep_id)
    result = _invoke_synthesis(lambda: get_visit_prep_synthesis(**args))
    return _synthesis_response(result)


def _synthesis_response(result: VisitPrepSynthesisView) -> VisitPrepSynthesisResponse:
    """Pure shared dashboard/companion projection; no read or authorization."""
    return VisitPrepSynthesisResponse(
        sources=_source_response(result.package),
        status=result.status,
        build_status=result.build_status,
        retry_at=result.retry_at,
        artifact_id=str(result.artifact_id) if result.artifact_id else None,
        version=result.version,
        recorded_at=result.recorded_at,
        generator_version=GENERATOR_VERSION,
        prompt_version=PROMPT_VERSION,
        model_profile=MODEL_PROFILE,
        brief=result.prepared,
    )
