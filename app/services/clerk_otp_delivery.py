"""Transient OTP projection and metadata-only distributed delivery ownership."""

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.configuration.clerk_otp_delivery import (
    TEMPLATE_SLUG,
    OtpDeliverySettings,
    valid_recipient,
)

MAX_BODY_BYTES = 64 * 1024
QUEUE_FRESHNESS_SECONDS = 120
FUTURE_SKEW_SECONDS = 10
SIGNATURE_FRESHNESS_SECONDS = 300
SMTP_TIMEOUT_SECONDS = 8
DISPATCH_MARGIN_SECONDS = SMTP_TIMEOUT_SECONDS + 4
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")


class RejectedOtp(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class PreparedOtp:
    ledger_key: str
    fingerprint: str
    deadline: float
    recipient: str = field(repr=False)
    code: str = field(repr=False)

    def require_fresh(self, now: float) -> None:
        # This is delivery queue freshness, not a claim of exact OTP expiry.
        if now + DISPATCH_MARGIN_SECONDS > self.deadline:
            raise RejectedOtp("stale_event")


def _digest(key: bytes, value: bytes) -> str:
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RejectedOtp("unsupported_payload")
        result[key] = value
    return result


def prepare_otp(
    body: bytes,
    *,
    delivery_id: str,
    signature_timestamp: str,
    signatures: str,
    settings: OtpDeliverySettings,
    now: float,
) -> PreparedOtp:
    if len(body) > MAX_BODY_BYTES:
        raise RejectedOtp("body_too_large", 413)
    if (
        _IDENTIFIER.fullmatch(delivery_id) is None
        or re.fullmatch(r"[0-9]{1,12}", signature_timestamp) is None
        or len(signatures) > 2048
        or abs(now - int(signature_timestamp)) > SIGNATURE_FRESHNESS_SECONDS
    ):
        raise RejectedOtp("invalid_signature", 401)
    message = delivery_id.encode() + b"." + signature_timestamp.encode() + b"." + body
    expected = base64.b64encode(
        hmac.new(settings.signing_key, message, hashlib.sha256).digest()
    ).decode()
    if not signatures.isascii() or not any(
        version == "v1" and separator and hmac.compare_digest(value, expected)
        for version, separator, value in (
            part.partition(",") for part in signatures.split()
        )
    ):
        raise RejectedOtp("invalid_signature", 401)
    try:
        event = json.loads(body, object_pairs_hook=_json_object)
    except (ValueError, UnicodeDecodeError):
        raise RejectedOtp("unsupported_payload") from None
    if not isinstance(event, dict):
        raise RejectedOtp("unsupported_payload")
    if event.get("instance_id") != settings.instance_id:
        raise RejectedOtp("foreign_instance", 403)
    stamp = event.get("timestamp")
    if (
        event.get("object") != "event"
        or event.get("type") != "email.created"
        or type(stamp) is not int
        or stamp < 1_500_000_000_000  # Reject seconds, bools and unsupported units.
        or stamp > (now + FUTURE_SKEW_SECONDS) * 1000
    ):
        raise RejectedOtp("unsupported_payload")
    email = event.get("data")
    if not isinstance(email, dict) or email.get("object") != "email":
        raise RejectedOtp("unsupported_payload")
    if email.get("slug") != TEMPLATE_SLUG:
        raise RejectedOtp("unowned_template", 403)
    metadata = email.get("data")
    code = metadata.get("otp_code") if isinstance(metadata, dict) else None
    recipient, email_id = email.get("to_email_address"), email.get("id")
    if (
        email.get("delivered_by_clerk") is not False
        or email.get("suppression_reason") is not None
        or not isinstance(code, str)
        or re.fullmatch(r"[0-9]{6}", code) is None
        or not isinstance(recipient, str)
        or not valid_recipient(recipient)
        or not isinstance(email_id, str)
        or _IDENTIFIER.fullmatch(email_id) is None
    ):
        raise RejectedOtp("unsupported_payload")
    # Ignore subject/body/links/IP/user metadata. Only six digits become content.
    identity = f"{settings.instance_id}\0{email_id}".encode()
    projected = json.dumps(
        [stamp, TEMPLATE_SLUG, recipient, code], separators=(",", ":")
    ).encode()
    prepared = PreparedOtp(
        "clerk-otp:v1:" + _digest(settings.signing_key, identity),
        _digest(settings.signing_key, projected),
        stamp / 1000 + QUEUE_FRESHNESS_SECONDS,
        recipient,
        code,
    )
    prepared.require_fresh(now)
    return prepared


# Redis receives only a keyed fingerprint, random ownership nonce, counters,
# fixed states and times. No email/OTP, subject, body or provider ID is stored.
_CLAIM = """
local old = redis.call('GET', KEYS[1])
local now = tonumber(ARGV[3])
local row = {fingerprint=ARGV[1], owner=ARGV[2], attempts=0,
             state='claimed', lease_until=now+30, retry_after=0}
if old then
  local ok, prior = pcall(cjson.decode, old)
  if not ok or type(prior) ~= 'table' then return 'unavailable' end
  if prior.fingerprint ~= ARGV[1] then return 'conflict' end
  if prior.state == 'sent' then return 'duplicate' end
  if prior.state == 'uncertain' or prior.state == 'exhausted' then return prior.state end
  if prior.state == 'dispatching' then
    if prior.lease_until > now then return 'busy' end
    prior.state = 'uncertain'; prior.owner = ''
    redis.call('SET', KEYS[1], cjson.encode(prior), 'EX', 660)
    return 'uncertain'
  end
  if prior.state ~= 'claimed' and prior.state ~= 'retry' then return 'unavailable' end
  if prior.lease_until > now or prior.retry_after > now then return 'busy' end
  row.attempts = prior.attempts
  if row.attempts >= 3 then return 'exhausted' end
end
redis.call('SET', KEYS[1], cjson.encode(row), 'EX', 660)
return 'claimed'
"""
_TRANSITION = """
local old = redis.call('GET', KEYS[1])
if not old then return 0 end
local ok, row = pcall(cjson.decode, old)
if not ok or type(row) ~= 'table' or row.owner ~= ARGV[1] then return 0 end
local action = ARGV[2]
local now = tonumber(ARGV[3])
if action == 'dispatching' then
  if row.state ~= 'claimed' or row.lease_until <= now then return 0 end
  row.attempts = row.attempts + 1; row.state = action; row.lease_until = now+30
elseif row.state == 'dispatching' and row.lease_until > now then
  if action ~= 'sent' and action ~= 'retry' and action ~= 'uncertain' then return 0 end
  row.state = action; row.owner = ''; row.lease_until = 0; row.retry_after = now+2
  if action == 'retry' and row.attempts >= 3 then row.state = 'exhausted' end
else return 0 end
redis.call('SET', KEYS[1], cjson.encode(row), 'EX', 660)
return 1
"""


class RedisOtpLedger:
    def __init__(self, client: Any):
        self.client = client

    def claim(self, prepared: PreparedOtp, owner: str, now: float) -> str:
        result = self.client.eval(
            _CLAIM, 1, prepared.ledger_key, prepared.fingerprint, owner, now
        )
        return result.decode() if isinstance(result, bytes) else result

    def transition(self, key: str, owner: str, state: str, now: float) -> bool:
        return self.client.eval(_TRANSITION, 1, key, owner, state, now) == 1


def new_owner() -> str:
    return uuid.uuid4().hex


def clock() -> float:
    return time.time()
