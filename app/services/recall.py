import asyncio
import json
import time
from datetime import datetime

import httpx

from app import log
from app.configuration.recall import RECALL_API_BASE, RECALL_API_KEY
from app.services.external_webhook_inbox import provider_log_ref

# Defensive timeout for every outbound Recall API call. Today (2026-05-19)
# the worker hung because some external HTTP request never returned, which
# blocked all four celery prefork slots and silently backed up the queue.
# Explicit timeouts mean a hang surfaces as an exception (Sentry) instead
# of a worker that looks alive but can't process anything.
_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Transcript downloads can be larger blobs; allow more read time without
# loosening the per-API-call envelope above.
_HTTPX_DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
RECORDING_RETENTION_HOURS = 6
START_RECORDING_CONFIG = {
    "retention": {"type": "timed", "hours": RECORDING_RETENTION_HOURS}
}

# Bot status codes that mean the bot is present in the call and able to
# accept a start_recording command. See Recall.ai docs for the full list.
#
# `recording_permission_allowed` is the status the bot transitions through
# once the host approves the native Zoom recording-consent prompt that the
# Meeting SDK fires when `require_recording_permission` is set. With
# `manual_recording=True`, the bot stays in this state until we explicitly
# call start_recording — so we treat it as "ready" alongside the older
# `in_call_not_recording`. Without this, the wait loop times out even
# though the host has already approved, and the appointment is aborted.
_BOT_READY_STATUSES = {
    "in_call_not_recording",
    "in_call_recording",
    "recording_permission_allowed",
}
# Statuses that prove Zoom confirmed the host approved recording. Reaching
# one of these is what makes a start_recording command stick, so we only
# treat an earlier `in_call_not_recording` as ready once no approval is
# pending (see _wait_until_bot_ready).
_BOT_APPROVED_STATUSES = {
    "recording_permission_allowed",
    "in_call_recording",
}
_BOT_TERMINAL_STATUSES = {
    "call_ended",
    "fatal",
    "done",
}
# `recording_permission_denied` is NOT terminal, despite the name. Zoom's
# consent handshake transitions *through* it on the way to
# `recording_permission_allowed` on every single Zoom session — successful
# ones included — carrying the sub_code below for a window of ~0.5-0.8s.
# Verified against Recall bot history for 7 production sessions (2026-08).
#
# Treating it as terminal meant a 2s poll that happened to land inside that
# ~600ms window aborted a perfectly healthy, already-approved bot and kicked
# it out of the visit: a ~30% failure rate matching 600ms/2000ms. Any *other*
# sub_code is a genuine host rejection and is still terminal.
_BOT_PERMISSION_DENIED_STATUS = "recording_permission_denied"
_TRANSIENT_PERMISSION_DENIED_SUB_CODE = "zoom_local_recording_request_disabled"
# Approval lands ~0.5-0.8s after the transient denial, so poll faster than the
# normal interval while inside that window. Sleeping a full 2s there would
# delay start_recording past the moment the bot was authorized and drop the
# opening seconds of the visit from the transcript.
_PERMISSION_PHASE_POLL_INTERVAL = 1.0

# Display name the bot uses inside Zoom. Kept as a module constant so
# zoom_webhooks can match participants against the same string when
# classifying join/leave events. Must match the bot_name passed to Recall.
BOT_NAME = "Cosmas AI Note-Taker"

# In-call disclosure posted to the Zoom chat when the bot starts recording.
# This is a *reinforcement* of the notice patients already see — Zoom's own
# native recording-consent prompt is the primary disclosure/gate. Kept as a
# module constant so the copy can be reviewed/edited in one place.
CONSENT_CHAT_MESSAGE = (
    "Hi — I'm the Cosmas AI Note-Taker. This visit is being recorded to help "
    "your clinician write up the notes from your appointment. Your clinician "
    "reviews and approves everything before it's saved, and your information "
    "is protected under HIPAA."
)


def create_bot(
    meeting_url: str,
    zak_url: str,
    bot_name: str = BOT_NAME,
    manual_recording: bool = True,
    metadata: dict | None = None,
    deadline: float | None = None,
    before_mutation=None,
) -> dict:
    if not manual_recording:
        raise ValueError("Recall bots must use explicit manual recording control")
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "zoom": {
            # Recall fetches the ZAK just-in-time from this URL when the bot
            # joins; we don't pre-fetch one because ZAKs expire in ~2h and
            # a stale ZAK silently fails the join. Endpoint contract: GET,
            # plain-text body containing the raw ZAK string.
            "zak_url": zak_url,
            # Surfaces Zoom's native recording-consent prompt to the host
            # when the bot joins, satisfying the Marketplace UI-notices
            # requirements (Active Apps Notifier + recording disclosure).
            "require_recording_permission": True,
            "request_recording_permission_on_host_join": True,
        },
        # Official Recall recording-control contract: null keeps creation in
        # manual mode.  The full config, including retention, is supplied to
        # POST start_recording because that endpoint overrides this value.
        "recording_config": None,
    }
    if metadata is not None:
        payload["metadata"] = metadata

    if deadline is not None:
        if before_mutation is not None:
            before_mutation()
        data = _bounded_provider_json("POST", "/bot/", deadline, payload)
    else:
        data = _create_bot_unbounded(payload)

    log.info(
        "recall bot created",
        bot_ref=provider_log_ref(data.get("id")),
        manual_recording=True,
    )
    return data


def _create_bot_unbounded(payload: dict) -> dict:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/",
            json=payload,
            headers={
                "Authorization": f"Token {RECALL_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        if not response.is_success:
            log.error(
                "recall api error",
                status=response.status_code,
            )
        response.raise_for_status()
        data = response.json()

    return data


def ping() -> None:
    """Cheapest authenticated call the Recall API offers.

    Used by the synthetic monitor to prove two things a rotated or revoked
    ``RECALL_API_KEY`` would break: the API is reachable from this container,
    and our credentials still work. Without it, an invalid key is discovered by
    the first real patient visit that fails to record.

    Raises ``httpx.HTTPError`` so the caller decides how to alert.
    """
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.get(
            f"{RECALL_API_BASE}/bot/",
            params={"limit": 1},
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        response.raise_for_status()


def get_bot(bot_id: str) -> dict:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.get(
            f"{RECALL_API_BASE}/bot/{bot_id}/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        response.raise_for_status()
        return response.json()


def _bot_status_changes(bot: dict) -> list[dict]:
    return bot.get("status_changes") or []


def _current_bot_status(bot: dict) -> str | None:
    changes = _bot_status_changes(bot)
    if not changes:
        return None
    return changes[-1].get("code")


def _current_bot_sub_code(bot: dict) -> str | None:
    changes = _bot_status_changes(bot)
    if not changes:
        return None
    return changes[-1].get("sub_code")


def _latest_ready_status(bot: dict) -> str | None:
    """Most recent ready status anywhere in the bot's history, or None.

    We scan the whole history rather than only `status_changes[-1]` because
    the bot can move through several states between two of our polls: the
    Zoom consent handshake emits `in_call_not_recording` ->
    `recording_permission_denied` -> `recording_permission_allowed` inside
    ~800ms, so sampling only the latest entry every 2s loses whichever
    transitions fell between polls. Reaching a ready state is a fact about
    the bot that stays true, so the history is the reliable signal.
    """
    for change in reversed(_bot_status_changes(bot)):
        code = change.get("code")
        if code in _BOT_READY_STATUSES:
            return code
    return None


def _has_recording_approval(bot: dict) -> bool:
    "True once Zoom has confirmed the host approved recording."
    return any(
        change.get("code") in _BOT_APPROVED_STATUSES
        for change in _bot_status_changes(bot)
    )


def _wait_until_bot_ready(
    bot_id: str,
    timeout_seconds: float = 45.0,
    poll_interval: float = 2.0,
    deadline: float | None = None,
) -> str | None:
    """Poll Recall until the bot can accept start_recording (or has really
    terminated). Returns the final observed status, or None on timeout."""
    bounded_launch = deadline is not None
    deadline = min(
        time.monotonic() + timeout_seconds,
        deadline if deadline is not None else float("inf"),
    )
    last_status: str | None = None
    while time.monotonic() < deadline:
        try:
            bot = (
                _bounded_provider_json("GET", f"/bot/{bot_id}/", deadline)
                if bounded_launch
                else get_bot(bot_id)
            )
        except (httpx.HTTPError, TimeoutError) as exc:
            log.warning(
                "recall get_bot transient error while waiting for ready",
                bot_ref=provider_log_ref(bot_id),
                error_type=type(exc).__name__,
            )
            time.sleep(max(0.0, min(poll_interval, deadline - time.monotonic())))
            continue

        if time.monotonic() >= deadline:
            break

        status = _current_bot_status(bot)
        if status != last_status:
            log.info(
                "recall bot status",
                bot_ref=provider_log_ref(bot_id),
                status=status,
            )
            last_status = status

        if status in _BOT_TERMINAL_STATUSES:
            log.warning(
                "recall bot reached terminal state before becoming ready",
                bot_ref=provider_log_ref(bot_id),
                status=status,
            )
            return status

        if status == _BOT_PERMISSION_DENIED_STATUS:
            sub_code = _current_bot_sub_code(bot)
            if sub_code != _TRANSIENT_PERMISSION_DENIED_SUB_CODE:
                log.warning(
                    "recall bot recording permission denied by host",
                    bot_ref=provider_log_ref(bot_id),
                    status=status,
                    permission_denied=True,
                )
                return status
            # Transient phase of the Zoom consent handshake. Keep waiting for
            # the approval that lands a few hundred ms later instead of
            # aborting a bot that is already sitting in the call.
            log.info(
                "recall bot in transient recording-permission phase, waiting",
                bot_ref=provider_log_ref(bot_id),
                transient_permission_phase=True,
            )
            time.sleep(
                max(
                    0.0,
                    min(
                        poll_interval,
                        _PERMISSION_PHASE_POLL_INTERVAL,
                        deadline - time.monotonic(),
                    ),
                )
            )
            continue

        if _has_recording_approval(bot):
            # Approval may have been superseded between polls (e.g. the bot
            # already moved on to in_call_recording), so report the most
            # recent ready status — start_recording keys off it to avoid
            # double-starting a recording.
            return _latest_ready_status(bot) or status
        if status in _BOT_READY_STATUSES:
            return status
        time.sleep(max(0.0, min(poll_interval, deadline - time.monotonic())))

    log.warning(
        "recall bot did not become ready before timeout",
        bot_ref=provider_log_ref(bot_id),
        last_status=last_status,
        timeout_seconds=timeout_seconds,
    )
    return last_status


def _recordings_by_id(bot: dict, bot_id: str) -> dict[str, dict]:
    """Recall start_recording returns a Bot, whose top-level ID is not a recording."""
    if not isinstance(bot, dict) or bot.get("id") != bot_id:
        raise ValueError("recall_recording_bot_mismatch")
    rows = bot.get("recordings")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("recall_recordings_unconfirmed")
    result = {}
    for row in rows:
        key = row.get("id") if isinstance(row, dict) else None
        if not isinstance(key, str) or not key or key == bot_id or key in result:
            raise ValueError("recall_recording_identity_unconfirmed")
        for field in ("started_at", "completed_at"):
            value = row.get(field)
            if value is not None:
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None:
                        raise ValueError()
                except (TypeError, ValueError):
                    raise ValueError("recall_recording_timing_unconfirmed") from None
        result[key] = row
    return result


def _new_started_recording(bot: dict, bot_id: str, previous: set[str]) -> dict | None:
    recordings = _recordings_by_id(bot, bot_id)
    candidates = set(recordings) - previous
    if len(candidates) > 1 or not previous.issubset(recordings):
        raise ValueError("recall_recording_selection_ambiguous")
    changes = bot.get("status_changes")
    if (
        not isinstance(changes, list)
        or not changes
        or not isinstance(changes[-1], dict)
    ):
        raise ValueError("recall_recording_status_unconfirmed")
    current = changes[-1].get("code")
    if current in _BOT_TERMINAL_STATUSES or (
        current == _BOT_PERMISSION_DENIED_STATUS
        and changes[-1].get("sub_code") != _TRANSIENT_PERMISSION_DENIED_SUB_CODE
    ):
        raise ValueError("recall_recording_start_unconfirmed")
    if candidates:
        key = candidates.pop()
        row = recordings[key]
        if row.get("completed_at") is not None:
            raise ValueError("recall_recording_already_completed")
        status = row.get("status")
        if not isinstance(status, dict) or status.get("code") != "processing":
            raise ValueError("recall_recording_not_processing")
        if (
            row.get("started_at") is not None
            and "completed_at" in row
            and current == "in_call_recording"
        ):
            return {"id": key}
    return None


def _bounded_provider_json(
    method: str,
    path: str,
    deadline: float,
    payload: dict | None = None,
    ceiling: float = 30.0,
) -> dict:
    # asyncio.timeout bounds the entire request, including connect, headers and
    # streamed body. HTTPX's per-read timeout alone permits a slow response to
    # outlive the polling deadline. This synchronous adapter runs on the worker.
    async def read():
        timeout_seconds = min(ceiling, deadline - time.monotonic())
        if timeout_seconds <= 0:
            raise TimeoutError("recall_launch_deadline_exhausted")
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                async with client.stream(
                    method,
                    f"{RECALL_API_BASE}{path}",
                    json=payload,
                    headers={"Authorization": f"Token {RECALL_API_KEY}"},
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for part in response.aiter_bytes(chunk_size=65536):
                        data.extend(part)
                        if len(data) > 1048576:
                            raise ValueError("recall_recording_response_too_large")
                    return json.loads(data)

    return asyncio.run(read())


def _read_recording_bot(bot_id: str, timeout_seconds: float) -> dict:
    return _bounded_provider_json(
        "GET", f"/bot/{bot_id}/", time.monotonic() + timeout_seconds, ceiling=5.0
    )


def _confirm_new_recording(
    bot: dict, bot_id: str, previous: set[str], deadline: float | None = None
) -> dict:
    deadline = min(
        time.monotonic() + 15.0, deadline if deadline is not None else float("inf")
    )
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("recall_recording_start_unconfirmed")
        recording = _new_started_recording(bot, bot_id, previous)
        if recording is not None:
            return recording
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("recall_recording_start_unconfirmed")
        time.sleep(min(0.5, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("recall_recording_start_unconfirmed")
        bot = _read_recording_bot(bot_id, min(5.0, remaining))


def start_recording(
    bot_id: str,
    wait_for_ready: bool = True,
    *,
    deadline: float | None = None,
    before_mutation=None,
) -> dict:
    """Tell the bot to start recording. When wait_for_ready is True (the
    default, used by the live patient-joined flow) we first poll Recall
    until the bot is in_call_not_recording — otherwise a start_recording
    command issued while the bot is still joining is silently dropped
    and the meeting yields an empty transcript."""
    if wait_for_ready:
        if deadline is not None and deadline - time.monotonic() < 95.0:
            raise TimeoutError("recall_launch_budget_insufficient")
        status = (
            _wait_until_bot_ready(bot_id, deadline=deadline - 50.0)
            if deadline is not None
            else _wait_until_bot_ready(bot_id)
        )
        if status not in _BOT_READY_STATUSES:
            log.warning(
                "recall start_recording aborted: bot never reached ready state",
                bot_ref=provider_log_ref(bot_id),
                status=status,
            )
            return {}
        if status == "in_call_recording":
            log.info(
                "recall bot already recording, skipping start_recording",
                bot_ref=provider_log_ref(bot_id),
            )
            raise ValueError("recall_recording_already_active")

    before = _read_recording_bot(
        bot_id, min(5.0, deadline - time.monotonic()) if deadline else 5.0
    )
    previous = _recordings_by_id(before, bot_id)
    if _current_bot_status(before) not in {
        "in_call_not_recording",
        "recording_permission_allowed",
    } or any(row.get("completed_at") is None for row in previous.values()):
        raise ValueError("recall_recording_already_active")

    if deadline is not None:
        if before_mutation is not None:
            before_mutation()
        data = _bounded_provider_json(
            "POST",
            f"/bot/{bot_id}/start_recording/",
            deadline - 15.0,
            START_RECORDING_CONFIG,
        )
    else:
        data = _start_recording_unbounded(bot_id)
    # The POST is issued exactly once. Missing, conflicting or timed-out
    # correspondence raises so the caller retains its durable teardown intent;
    # it must never consume an old recording or adopt the Bot's own ID.
    recording = _confirm_new_recording(data, bot_id, set(previous), deadline)
    log.info(
        "recall recording started",
        bot_ref=provider_log_ref(bot_id),
        recording_ref=provider_log_ref(recording["id"]),
        retention_hours=RECORDING_RETENTION_HOURS,
    )
    return recording


def _start_recording_unbounded(bot_id: str) -> dict:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/start_recording/",
            json=START_RECORDING_CONFIG,
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        if not response.is_success:
            log.error(
                "recall start_recording error",
                bot_ref=provider_log_ref(bot_id),
                status=response.status_code,
            )
        response.raise_for_status()
        data = response.json()
    return data


def send_chat_message(
    bot_id: str, message: str, to: str = "everyone", *, deadline: float | None = None
) -> None:
    """Post a chat message into the meeting as the bot. Used to surface the
    in-call recording disclosure to all participants when recording starts.

    This is best-effort: callers should treat a failure here as non-fatal.
    The real disclosure/consent gate is Zoom's native recording-consent
    prompt (see require_recording_permission in create_bot), so a dropped
    chat message must never abort the recording."""
    if deadline is not None:
        _bounded_provider_json(
            "POST",
            f"/bot/{bot_id}/send_chat_message/",
            deadline,
            {"to": to, "message": message},
            ceiling=5.0,
        )
        return
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/send_chat_message/",
            json={"to": to, "message": message},
            headers={
                "Authorization": f"Token {RECALL_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        if not response.is_success:
            log.error(
                "recall send_chat_message error",
                bot_ref=provider_log_ref(bot_id),
                status=response.status_code,
            )
        response.raise_for_status()
    log.info("recall chat message sent", bot_ref=provider_log_ref(bot_id))


def stop_recording(bot_id: str) -> None:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/stop_recording/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
    if response.status_code == httpx.codes.BAD_REQUEST:
        # Bot is not currently recording (already stopped or never started).
        # Treat as success — this can happen in race conditions between
        # participant_left and meeting.ended both trying to stop the same recording.
        log.info(
            "recall stop_recording skipped, bot not recording",
            bot_ref=provider_log_ref(bot_id),
        )
        return
    if not response.is_success:
        log.error(
            "recall stop_recording error",
            status=response.status_code,
        )
    response.raise_for_status()
    log.info("recall recording stopped", bot_ref=provider_log_ref(bot_id))


def create_async_transcript(recording_id: str, language_code: str = "auto") -> dict:
    payload = {"provider": {"recallai_async": {"language_code": language_code}}}
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            f"{RECALL_API_BASE}/recording/{recording_id}/create_transcript/",
            json=payload,
            headers={
                "Authorization": f"Token {RECALL_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        if not response.is_success:
            log.error(
                "recall create_transcript error",
                status=response.status_code,
            )
        response.raise_for_status()
        data = response.json()
    log.info(
        "recall async transcript created",
        recording_ref=provider_log_ref(recording_id),
        transcript_ref=provider_log_ref(data.get("id")),
    )
    return data


def get_transcript(transcript_id: str) -> dict:
    """GET /transcript/{id}/ — returns metadata including data.download_url."""
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.get(
            f"{RECALL_API_BASE}/transcript/{transcript_id}/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        if not response.is_success:
            log.error(
                "recall get_transcript error",
                status=response.status_code,
            )
        response.raise_for_status()
    return response.json()


def download_transcript_data(download_url: str) -> list[dict]:
    """Fetch the pre-signed transcript download URL (no auth required)."""
    with httpx.Client(timeout=_HTTPX_DOWNLOAD_TIMEOUT) as client:
        response = client.get(download_url)
        response.raise_for_status()
    return response.json()


def leave_call(bot_id: str) -> None:
    """Tell the bot to leave the meeting. Triggers the Recall `done`
    terminal status and stops billing for this bot. Safe to call on a
    bot that already left."""
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/leave_call/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
    if response.status_code == httpx.codes.BAD_REQUEST:
        log.info(
            "recall leave_call skipped, bot already done",
            bot_ref=provider_log_ref(bot_id),
        )
        return
    if not response.is_success:
        log.error(
            "recall leave_call error",
            bot_ref=provider_log_ref(bot_id),
            status=response.status_code,
        )
    response.raise_for_status()
    log.info("recall bot instructed to leave call", bot_ref=provider_log_ref(bot_id))
