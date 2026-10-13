"""Staging-only custom OTP delivery; successful ACK means SMTP accepted only."""

import asyncio
from functools import lru_cache

import redis
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.configuration import clerk_otp_delivery as configuration
from app.configuration.emailer import (
    SmtpNotAccepted,
    SmtpOutcomeUnknown,
    send_clerk_verification_code,
)
from app.configuration.redis import redis_url
from app.environments import python_environment
from app.services.clerk_otp_delivery import (
    MAX_BODY_BYTES,
    RedisOtpLedger,
    RejectedOtp,
    clock,
    new_owner,
    prepare_otp,
)

PATH = "/external/v1/webhooks/clerk-otp"
router = APIRouter()


@lru_cache(maxsize=1)
def ledger() -> RedisOtpLedger:
    # Existing Redis credentials remain server-side; this pool has finite I/O.
    return RedisOtpLedger(
        redis.from_url(
            redis_url(),
            socket_timeout=2,
            socket_connect_timeout=2,
            retry_on_timeout=False,
        )
    )


def response(code: str, status: int = 503) -> JSONResponse:
    return JSONResponse(
        {"status": code},
        status_code=status,
        headers={"Cache-Control": "no-store", "Retry-After": "2"},
    )


async def bounded_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            raise RejectedOtp("invalid_request", 400) from None
        if declared < 0:
            raise RejectedOtp("invalid_request", 400)
        if declared > MAX_BODY_BYTES:
            raise RejectedOtp("body_too_large", 413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise RejectedOtp("body_too_large", 413)
        body.extend(chunk)
    return bytes(body)


@router.post(PATH, include_in_schema=False)
async def clerk_otp_webhook(request: Request) -> JSONResponse:
    settings = configuration.SETTINGS
    if settings is None or python_environment() != "staging":
        return response("unavailable")
    try:
        body = await asyncio.wait_for(bounded_body(request), timeout=5)
        prepared = prepare_otp(
            body,
            delivery_id=request.headers.get("svix-id", ""),
            signature_timestamp=request.headers.get("svix-timestamp", ""),
            signatures=request.headers.get("svix-signature", ""),
            settings=settings,
            now=clock(),
        )
    except RejectedOtp as error:
        return response(error.code, error.status)
    except Exception:
        # Never expose provider payload, parsing failures or request locals.
        return response("invalid_request", 400)
    owner = new_owner()
    try:
        store = ledger()
        outcome = await run_in_threadpool(store.claim, prepared, owner, clock())
        if outcome == "duplicate":
            return response("duplicate", 200)
        if outcome == "conflict":
            return response("delivery_conflict", 409)
        if outcome != "claimed":
            return response("delivery_unavailable")
        prepared.require_fresh(clock())
        if not await run_in_threadpool(
            store.transition, prepared.ledger_key, owner, "dispatching", clock()
        ):
            return response("delivery_unavailable")
        prepared.require_fresh(clock())
        state = "sent"
        try:
            await send_clerk_verification_code(prepared.recipient, prepared.code)
        except SmtpNotAccepted:
            state = "retry"
        except SmtpOutcomeUnknown:
            state = "uncertain"
        if not await run_in_threadpool(
            store.transition, prepared.ledger_key, owner, state, clock()
        ):
            return response("delivery_uncertain")
        return (
            response("smtp_accepted", 200)
            if state == "sent"
            else response("delivery_" + state)
        )
    except RejectedOtp as error:
        return response(error.code, error.status)
    except Exception:
        return response("delivery_unavailable")
