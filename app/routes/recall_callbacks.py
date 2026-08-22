"""Endpoints that Recall.ai calls back into our server during bot lifecycle.

Right now this hosts the just-in-time ZAK token endpoint. Recall's bot hits
``GET /external/v1/recall/zak/{token}`` when joining a Zoom meeting and we
return a fresh ZAK as plain text — the contract their sample app documents
(see ``recallai/sample-apps/bot_signed_in_zoom_bots``).
"""

from typing import cast

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from starlette import status

from app import log
from app.configuration.redis import get_redis
from app.services import zoom as zoom_service

from app.models.doctor import Doctor

recall_callbacks_app = APIRouter(prefix="/external/v1/recall", tags=["recall"])


def zak_token_redis_key(token: str) -> str:
    return f"recall:zak_url_token:{token}"


@recall_callbacks_app.get(
    "/zak/{token}",
    response_class=PlainTextResponse,
    name="recall_zak_callback",
)
def recall_zak_callback(token: str) -> PlainTextResponse:
    """Recall's bot fetches the doctor's ZAK from this URL on join.

    Security model: ``token`` is an opaque random value (see
    ``zak_token_redis_key``) generated at bot-creation time and mapped to the
    doctor whose Zoom integration should be used. The token in the URL is the
    only auth — Recall does not send headers — so it must be unguessable and
    short-lived (TTL set by the writer).
    """
    redis = get_redis()
    raw = redis.get(zak_token_redis_key(token))
    if raw is None:
        log.info("recall zak callback: unknown or expired token")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    doctor_id = raw.decode() if isinstance(raw, bytes) else raw
    doctor = cast(Doctor | None, Doctor.get(id=doctor_id))
    if not doctor or doctor.deleted_at is not None:
        log.warning("recall zak callback: doctor unavailable", doctor_id=doctor_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    integration = zoom_service.get_zoom_integration(doctor)
    if not integration:
        log.warning(
            "recall zak callback: doctor has no zoom integration",
            doctor_id=doctor_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    try:
        zak = zoom_service.get_zak_token(doctor)
    except zoom_service.ZoomIntegrationInvalid:
        log.warning(
            "recall zak callback: doctor revoked Zoom OAuth",
            doctor_id=doctor_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    log.info("recall zak callback: token issued", doctor_id=doctor_id)
    return PlainTextResponse(content=zak)
