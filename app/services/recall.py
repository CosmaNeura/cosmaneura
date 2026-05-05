import time

import httpx

from app import log
from app.configuration.recall import RECALL_API_BASE, RECALL_API_KEY

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
# `recording_permission_denied` is terminal-ish: the host rejected the
# prompt, so no recording can ever happen on this bot. Treat it like the
# other terminal states so we bail out quickly instead of waiting 45s.
_BOT_TERMINAL_STATUSES = {
    "call_ended",
    "fatal",
    "done",
    "recording_permission_denied",
}

# Display name the bot uses inside Zoom. Kept as a module constant so
# zoom_webhooks can match participants against the same string when
# classifying join/leave events. Must match the bot_name passed to Recall.
BOT_NAME = "Cosmas AI Note-Taker"


def create_bot(
    meeting_url: str,
    zak_url: str,
    bot_name: str = BOT_NAME,
    manual_recording: bool = False,
    metadata: dict | None = None,
) -> dict:
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
    }
    if manual_recording:
        payload["recording_config"] = None
    if metadata is not None:
        payload["metadata"] = metadata

    with httpx.Client() as client:
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
                body=response.text,
                payload=payload,
            )
        response.raise_for_status()
        data = response.json()

    log.info("recall bot created", bot_id=data.get("id"), meeting_url=meeting_url)
    return data


def get_bot(bot_id: str) -> dict:
    with httpx.Client() as client:
        response = client.get(
            f"{RECALL_API_BASE}/bot/{bot_id}/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        response.raise_for_status()
        return response.json()


def _current_bot_status(bot: dict) -> str | None:
    changes = bot.get("status_changes") or []
    if not changes:
        return None
    return changes[-1].get("code")


def _wait_until_bot_ready(
    bot_id: str,
    timeout_seconds: float = 45.0,
    poll_interval: float = 2.0,
) -> str | None:
    """Poll Recall until the bot is actually in the call (or has terminated).
    Returns the final observed status, or None on timeout."""
    deadline = time.monotonic() + timeout_seconds
    last_status: str | None = None
    while time.monotonic() < deadline:
        try:
            bot = get_bot(bot_id)
        except httpx.HTTPError as exc:
            log.warning(
                "recall get_bot transient error while waiting for ready",
                bot_id=bot_id,
                error=str(exc),
            )
            time.sleep(poll_interval)
            continue

        status = _current_bot_status(bot)
        if status != last_status:
            log.info("recall bot status", bot_id=bot_id, status=status)
            last_status = status

        if status in _BOT_READY_STATUSES:
            return status
        if status in _BOT_TERMINAL_STATUSES:
            log.warning(
                "recall bot reached terminal state before becoming ready",
                bot_id=bot_id,
                status=status,
            )
            return status
        time.sleep(poll_interval)

    log.warning(
        "recall bot did not become ready before timeout",
        bot_id=bot_id,
        last_status=last_status,
        timeout_seconds=timeout_seconds,
    )
    return last_status


def start_recording(bot_id: str, wait_for_ready: bool = True) -> dict:
    """Tell the bot to start recording. When wait_for_ready is True (the
    default, used by the live patient-joined flow) we first poll Recall
    until the bot is in_call_not_recording — otherwise a start_recording
    command issued while the bot is still joining is silently dropped
    and the meeting yields an empty transcript."""
    if wait_for_ready:
        status = _wait_until_bot_ready(bot_id)
        if status not in _BOT_READY_STATUSES:
            log.warning(
                "recall start_recording aborted: bot never reached ready state",
                bot_id=bot_id,
                status=status,
            )
            return {}
        if status == "in_call_recording":
            log.info(
                "recall bot already recording, skipping start_recording",
                bot_id=bot_id,
            )
            return {}

    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/start_recording/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        if not response.is_success:
            log.error(
                "recall start_recording error",
                bot_id=bot_id,
                status=response.status_code,
                body=response.text,
            )
        response.raise_for_status()
        data = response.json()
    log.info(
        "recall recording started",
        bot_id=bot_id,
        recording_id=data.get("id"),
    )
    return data


def stop_recording(bot_id: str) -> None:
    with httpx.Client() as client:
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
            bot_id=bot_id,
            body=response.text,
        )
        return
    if not response.is_success:
        log.error(
            "recall stop_recording error",
            status=response.status_code,
            body=response.text,
        )
    response.raise_for_status()
    log.info("recall recording stopped", bot_id=bot_id)


def create_async_transcript(recording_id: str, language_code: str = "auto") -> dict:
    payload = {"provider": {"recallai_async": {"language_code": language_code}}}
    with httpx.Client() as client:
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
                body=response.text,
            )
        response.raise_for_status()
        data = response.json()
    log.info(
        "recall async transcript created",
        recording_id=recording_id,
        transcript_id=data.get("id"),
    )
    return data


def get_transcript(transcript_id: str) -> dict:
    """GET /transcript/{id}/ — returns metadata including data.download_url."""
    with httpx.Client() as client:
        response = client.get(
            f"{RECALL_API_BASE}/transcript/{transcript_id}/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
        if not response.is_success:
            log.error(
                "recall get_transcript error",
                status=response.status_code,
                body=response.text,
            )
        response.raise_for_status()
    return response.json()


def download_transcript_data(download_url: str) -> list[dict]:
    """Fetch the pre-signed transcript download URL (no auth required)."""
    with httpx.Client() as client:
        response = client.get(download_url)
        response.raise_for_status()
    return response.json()


def leave_call(bot_id: str) -> None:
    """Tell the bot to leave the meeting. Triggers the Recall `done`
    terminal status and stops billing for this bot. Safe to call on a
    bot that already left."""
    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/leave_call/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
    if response.status_code == httpx.codes.BAD_REQUEST:
        log.info(
            "recall leave_call skipped, bot already done",
            bot_id=bot_id,
            body=response.text,
        )
        return
    if not response.is_success:
        log.error(
            "recall leave_call error",
            bot_id=bot_id,
            status=response.status_code,
            body=response.text,
        )
    response.raise_for_status()
    log.info("recall bot instructed to leave call", bot_id=bot_id)
