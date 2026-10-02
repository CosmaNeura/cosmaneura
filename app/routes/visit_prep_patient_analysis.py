"""Server-only one-use claim and content-free usage callbacks."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.configuration import service_auth
from app.routes.companion_records import _closed_pairs
from app.routes.companion_visit_prep import _bearer
from app.routes.dependencies.view_as import view_as_browser_session_present
from app.services import visit_prep_patient_analysis as analysis

router = APIRouter()
PREFIX = "/service/v1/visit-prep/patient-analysis"


@router.post(f"{PREFIX}/claim", include_in_schema=False)
@router.post(f"{PREFIX}/usage", include_in_schema=False)
async def patient_analysis_callback(request: Request):
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    try:
        if (
            view_as_browser_session_present(request)
            or request.scope.get("query_string")
            or request.headers.getlist("content-type") != ["application/json"]
            or request.headers.getlist("content-encoding")
            or len(request.headers.getlist("x-visit-prep-analysis-grant")) != 1
        ):
            raise ValueError
        token = _bearer(request.headers, "authorization")
        grant = request.headers["x-visit-prep-analysis-grant"]
        if not 1 <= len(grant) <= 8192 or not grant.isascii():
            raise ValueError
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 4096:
                raise ValueError
        data = json.loads(body, object_pairs_hook=_closed_pairs)
        workload = await run_in_threadpool(
            service_auth.verify_visit_prep_workload,
            token,
            remaining_seconds=4,
            settings=service_auth.SOVEREIGN_TOOL_AUTH_SETTINGS,
        )
        if request.url.path.endswith("/claim"):
            if set(data) != {"topic_digest"}:
                raise ValueError
            result = await run_in_threadpool(
                analysis.claim_patient_analysis,
                grant=grant,
                topic_digest=data["topic_digest"],
                workload=workload,
            )
        else:
            usage = analysis.PatientUsage.model_validate_json(json.dumps(data))
            result = await run_in_threadpool(
                analysis.record_patient_analysis_usage,
                grant=grant,
                usage=usage,
                workload=workload,
            )
        return JSONResponse(content=result, headers=headers)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"detail": "Visit Prep is unavailable."},
            headers=headers,
        )
