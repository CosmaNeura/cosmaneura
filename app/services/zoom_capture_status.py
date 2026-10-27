"""Project signed Recall terminal events onto an already-bound Zoom capture.

This projection never authorizes, starts, resumes, or retries recording. The
capture_started fence survives every event, including a permission denial.
"""

import json
import re
from datetime import UTC, datetime
from typing import Literal, cast

from redis.exceptions import RedisError

from app import log
from app.configuration.redis import get_redis
from app.services.external_webhook_inbox import (
    PermanentWebhookError,
    RetryableWebhookError,
    provider_log_ref,
)

CaptureStatus = Literal["permission_denied", "ended", "failed"]
EVENTS = frozenset(
    {"bot.recording_permission_denied", "bot.call_ended", "bot.done", "bot.fatal"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TRANSIENT_DENIAL = "zoom_local_recording_request_disabled"

_PROJECT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
 or redis.call('GET', KEYS[2]) ~= ARGV[2]
 or redis.call('GET', KEYS[3]) ~= ARGV[3]
 or redis.call('GET', KEYS[4]) ~= ARGV[4] then return 0 end
local previous = redis.call('GET', KEYS[6])
local value = cjson.decode(ARGV[5])
if previous then
 local old = cjson.decode(previous)
 -- Denial/failure is not erased by later done or out-of-order delivery.
 if old.status == 'permission_denied' then value.status = old.status
 elseif old.status == 'failed' and value.status == 'ended' then
  value.status = old.status
 end
 if old.updated_at > value.updated_at then value.updated_at = old.updated_at end
end
redis.call('DEL', KEYS[5])
redis.call('SET', KEYS[6], cjson.encode(value), 'EX', 86400)
return 1
"""


def handle_bot_terminal(payload: dict) -> None:
    """Accept only the documented signed bot status DTO and our own binding."""
    event = payload.get("event")
    data = payload.get("data")
    detail = data.get("data") if isinstance(data, dict) else None
    bot = data.get("bot") if isinstance(data, dict) else None
    bot_id = bot.get("id") if isinstance(bot, dict) else None
    if (
        not isinstance(event, str)
        or event not in EVENTS
        or not isinstance(detail, dict)
        or detail.get("code") != event.removeprefix("bot.")
        or not isinstance(bot_id, str)
        or not _IDENTIFIER.fullmatch(bot_id)
    ):
        raise PermanentWebhookError()
    try:
        updated = datetime.fromisoformat(detail["updated_at"].replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("timezone_required")
        updated_at = updated.astimezone(UTC).isoformat(timespec="microseconds")
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise PermanentWebhookError() from exc
    if event == "bot.recording_permission_denied":
        if detail.get("sub_code") == _TRANSIENT_DENIAL:
            return
        status = "permission_denied"
    else:
        status = "failed" if event == "bot.fatal" else "ended"

    redis = get_redis()
    binding_key = f"recall:bot:{bot_id}:zoom_capture"
    raw = cast(str | bytes | None, redis.get(binding_key))
    if not raw:
        # A callback can precede Create Bot's response/publication. Never infer
        # the doctor, meeting or occurrence from provider-supplied metadata.
        raise RetryableWebhookError("bot_capture_binding_unavailable")
    try:
        binding = json.loads(raw)
        if (
            not isinstance(binding, dict)
            or set(binding) != {"meeting_id", "doctor_id", "occurrence"}
            or not all(
                isinstance(value, str) and _IDENTIFIER.fullmatch(value)
                for value in binding.values()
            )
        ):
            raise ValueError("invalid_binding")
    except (TypeError, ValueError) as exc:
        raise PermanentWebhookError() from exc
    prefix = f"recall:meeting:{binding['meeting_id']}"
    result = {
        "doctor_id": binding["doctor_id"],
        "occurrence": binding["occurrence"],
        "status": status,
        "updated_at": updated_at,
    }
    projected = redis.eval(
        _PROJECT,
        6,
        binding_key,
        prefix + ":occurrence_digest",
        prefix + ":doctor_id",
        prefix + ":bot_id",
        prefix + ":recording_active",
        prefix + ":capture_terminal",
        cast(str, raw),
        binding["occurrence"],
        binding["doctor_id"],
        bot_id,
        json.dumps(result),
    )
    log.info(
        "recall terminal capture event projected"
        if projected
        else "recall terminal capture event stale",
        bot_ref=provider_log_ref(bot_id),
        capture_status=status,
    )


def for_meeting(meeting) -> CaptureStatus | None:
    """Read only a result matching the schedule row's consumed authorization."""
    if not meeting.capture_authorization_consumed_at:
        return None
    try:
        raw = cast(
            str | bytes | None,
            get_redis().get(
                f"recall:meeting:{meeting.zoom_meeting_id}:capture_terminal"
            ),
        )
        value = json.loads(raw) if raw else {}
        if (
            value.get("doctor_id") == str(meeting.doctor_id)
            and value.get("occurrence")
            == meeting.capture_authorization_occurrence_digest
            and value.get("status") in {"permission_denied", "ended", "failed"}
        ):
            return value["status"]
    except (RedisError, ValueError, TypeError, AttributeError):
        log.warning("zoom capture status unavailable")
    return None
