"""Fail-closed rate limits for the public Visit Prep interview surface.

The public router obtains ``network_signal`` only from the ASGI server's
normalized client identity, never by parsing a forwarding header itself. Public
activation therefore requires the deployment to prove the exact trusted-proxy
chain. All Redis keys are keyed-HMAC references; bearer credentials and network
signals are never stored in Redis.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import re
from collections.abc import Sequence

import redis

from app.configuration.redis import redis_url
from app.services.visit_prep_patient_continuation import ConfirmedVisitPrepAccess
from app.utils.encryption import pseudonymous_digest

WINDOW_SECONDS = 15 * 60
NETWORK_LIMIT = 300
TOKEN_LIMIT = 60
CONFIRMATION_LIMIT = 8
REDIS_CONNECT_TIMEOUT_SECONDS = 1.0
REDIS_SOCKET_TIMEOUT_SECONDS = 1.0

_TOKEN_PREFIX = "vp1_"
_TOKEN_BODY = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PSEUDONYMOUS_REFERENCE = re.compile(r"^[0-9a-f]{64}$")
_MAX_NETWORK_SIGNAL_BYTES = 256
_UNAVAILABLE_NETWORK_SIGNAL = "transport-peer-unavailable"

_NETWORK_CONTEXT = "visit-prep-rate-network-v1"
_CREDENTIAL_NETWORK_CONTEXT = "visit-prep-rate-credential-network-v1"
_CONFIRMATION_NETWORK_CONTEXT = "visit-prep-rate-confirmation-network-v1"
_TOKEN_CONTEXT = "visit-prep-rate-token-v1"
_TOKEN_CONFIRMATION_CONTEXT = "visit-prep-rate-token-confirmation-v1"

_KEY_PREFIX = "visit-prep:rate:v1"
_RATE_LIMIT_REDIS = None

# Each invocation increments every supplied bucket in one Redis operation.
# Expiry is attached only when a fixed window begins, so repeated failures
# cannot keep a counter alive indefinitely by refreshing its TTL.
_ATOMIC_INCREMENT_FIXED_WINDOW = """
local window = tonumber(ARGV[1])
local counts = {}
for index, key in ipairs(KEYS) do
  local count = redis.call('INCR', key)
  if count == 1 then
    redis.call('EXPIRE', key, window)
  end
  counts[index] = count
end
return counts
"""


class RateLimitError(Exception):
    """Base class for bounded, non-sensitive rate-limit failures."""


class RateLimited(RateLimitError):
    """The request exceeded a fixed-window public endpoint limit."""

    code = "rate_limited"
    retry_after_seconds = WINDOW_SECONDS

    def __init__(self) -> None:
        super().__init__(self.code)


class RateLimitUnavailable(RateLimitError):
    """Rate-limit enforcement could not be proven and the request must stop."""

    code = "rate_limit_unavailable"

    def __init__(self) -> None:
        super().__init__(self.code)


def get_rate_limit_redis():
    """Return a dedicated fail-fast client for the public request boundary."""

    global _RATE_LIMIT_REDIS  # pylint: disable=global-statement
    if _RATE_LIMIT_REDIS is None:
        _RATE_LIMIT_REDIS = redis.from_url(
            redis_url(),
            retry_on_timeout=False,
            socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        )
    return _RATE_LIMIT_REDIS


def _bounded_network_signal(network_signal: str | None) -> str:
    if not isinstance(network_signal, str) or not network_signal:
        return _UNAVAILABLE_NETWORK_SIGNAL
    try:
        encoded = network_signal.encode("utf-8")
    except UnicodeEncodeError:
        return _UNAVAILABLE_NETWORK_SIGNAL
    if len(encoded) > _MAX_NETWORK_SIGNAL_BYTES:
        return _UNAVAILABLE_NETWORK_SIGNAL
    return network_signal


def _canonical_access_token(access_token: str | None) -> bool:
    if not isinstance(access_token, str) or not access_token.startswith(_TOKEN_PREFIX):
        return False
    body = access_token.removeprefix(_TOKEN_PREFIX)
    if _TOKEN_BODY.fullmatch(body) is None:
        return False
    try:
        decoded = base64.urlsafe_b64decode(body + "=")
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError):
        return False
    return len(decoded) == 32 and hmac.compare_digest(canonical, body)


def _reference(value: str, *, context: str) -> str:
    try:
        reference = pseudonymous_digest(value, context=context)
    except Exception:
        raise RateLimitUnavailable from None
    if (
        not isinstance(reference, str)
        or _PSEUDONYMOUS_REFERENCE.fullmatch(reference) is None
    ):
        raise RateLimitUnavailable
    return reference


def _redis_key(bucket: str, reference: str) -> str:
    return f"{_KEY_PREFIX}:{bucket}:{reference}"


def _increment(keys: Sequence[str], limits: Sequence[int]) -> None:
    if not keys or len(keys) != len(limits):
        raise RateLimitUnavailable
    try:
        raw_counts = get_rate_limit_redis().eval(
            _ATOMIC_INCREMENT_FIXED_WINDOW,
            len(keys),
            *keys,
            str(WINDOW_SECONDS),
        )
    except Exception:
        raise RateLimitUnavailable from None

    if not isinstance(raw_counts, (list, tuple)) or len(raw_counts) != len(keys):
        raise RateLimitUnavailable
    counts: list[int] = []
    for raw_count in raw_counts:
        if isinstance(raw_count, bool) or not isinstance(
            raw_count,
            (bytes, int, str),
        ):
            raise RateLimitUnavailable
        try:
            count = int(raw_count)
        except (TypeError, ValueError, OverflowError):
            raise RateLimitUnavailable from None
        if count < 1:
            raise RateLimitUnavailable
        counts.append(count)

    if any(count > limit for count, limit in zip(counts, limits, strict=True)):
        raise RateLimited


def enforce_network_rate_limit(*, network_signal: str | None) -> None:
    """Enforce the network bucket before any credential existence lookup."""

    network = _bounded_network_signal(network_signal)
    network_ref = _reference(network, context=_NETWORK_CONTEXT)
    _increment(
        (_redis_key("network", network_ref),),
        (NETWORK_LIMIT,),
    )


def enforce_credential_rate_limits(
    *,
    network_signal: str | None,
    access_token: str | ConfirmedVisitPrepAccess | None,
    confirmation: bool = False,
) -> None:
    """Enforce credential buckets without consulting durable token state.

    Every request consumes a network-scoped credential bucket. Canonical bearer
    candidates also consume a pseudonymous per-token bucket regardless of
    whether a matching row exists, so rate-limit behavior cannot reveal token
    existence. Malformed credentials never create attacker-selected Redis keys.
    """

    if not isinstance(confirmation, bool):
        raise RateLimitUnavailable

    network = _bounded_network_signal(network_signal)
    credential_network_ref = _reference(
        network,
        context=_CREDENTIAL_NETWORK_CONTEXT,
    )
    keys = [_redis_key("credential-network", credential_network_ref)]
    limits = [TOKEN_LIMIT]

    continued = isinstance(access_token, ConfirmedVisitPrepAccess)
    canonical_token = continued or _canonical_access_token(access_token)
    token_value = access_token.token_hash if continued else access_token
    if canonical_token:
        assert access_token is not None
        assert isinstance(token_value, str)
        token_ref = _reference(token_value, context=_TOKEN_CONTEXT)
        keys.append(_redis_key("token", token_ref))
        limits.append(TOKEN_LIMIT)

    if confirmation:
        confirmation_network_ref = _reference(
            network,
            context=_CONFIRMATION_NETWORK_CONTEXT,
        )
        keys.append(_redis_key("confirmation-network", confirmation_network_ref))
        limits.append(CONFIRMATION_LIMIT)
        if canonical_token:
            assert access_token is not None
            assert isinstance(token_value, str)
            confirmation_ref = _reference(
                token_value,
                context=_TOKEN_CONFIRMATION_CONTEXT,
            )
            keys.append(_redis_key("confirmation", confirmation_ref))
            limits.append(CONFIRMATION_LIMIT)

    _increment(keys, limits)


def enforce_rate_limits(
    *,
    network_signal: str | None,
    access_token: str | None,
    confirmation: bool = False,
) -> None:
    """Convenience wrapper for callers that already know credential status."""

    enforce_network_rate_limit(network_signal=network_signal)
    enforce_credential_rate_limits(
        network_signal=network_signal,
        access_token=access_token,
        confirmation=confirmation,
    )
