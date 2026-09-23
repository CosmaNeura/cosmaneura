"""Short-lived, browser-bound state for a SchedulingProvider's OAuth redirect flow.

Same pattern as app/services/zoom_oauth_state.py, generalized by provider so
Acuity and Google Calendar each get an isolated namespace (Redis key, cookie,
digest context) without touching the existing Zoom flow.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from typeid import TypeID
from typeid.errors import TypeIDException

from app.configuration.redis import get_redis
from app.utils.encryption import (
    DecryptionError,
    decrypt_value,
    encrypt_value,
    pseudonymous_digest,
)

from app.models.patient_external_identity import SchedulingProviderSource

SCHEDULING_OAUTH_STATE_TTL_SECONDS = 5 * 60

_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_MAX_ISSUE_ATTEMPTS = 3


@dataclass(frozen=True)
class IssuedSchedulingOAuthState:
    state: str
    browser_binding: str


def scheduling_oauth_binding_cookie(source: SchedulingProviderSource) -> str:
    return f"__Host-cosmas_{source.value}_oauth_binding"


def _digest_context(source: SchedulingProviderSource, suffix: str) -> str:
    return f"{source.value}-oauth-{suffix}"


def scheduling_oauth_state_redis_key(
    source: SchedulingProviderSource, state: str
) -> str:
    """Return a non-reversible Redis key for an opaque OAuth state value."""

    digest = pseudonymous_digest(state, context=_digest_context(source, "state"))
    return f"scheduling:{source.value}:oauth_state:{digest}"


_redis_key = scheduling_oauth_state_redis_key


def _browser_binding_digest(
    source: SchedulingProviderSource, browser_binding: str
) -> str:
    return pseudonymous_digest(
        browser_binding, context=_digest_context(source, "browser-binding")
    )


def _is_opaque_token(value: str | None) -> bool:
    return bool(value and _OPAQUE_TOKEN_RE.fullmatch(value))


# Only a request holding the browser binding paired with this state may
# delete it. The comparison and deletion happen in one Redis operation, so a
# wrong browser cannot invalidate the real browser's flow and two callbacks
# cannot both exchange an authorization code. Mirrors zoom_oauth_state.py.
_ATOMIC_CONSUME_MATCHING_BINDING = """
local value = redis.call('GET', KEYS[1])
if not value then
  return nil
end
local expected_prefix = ARGV[1] .. ':'
if string.sub(value, 1, string.len(expected_prefix)) ~= expected_prefix then
  return nil
end
redis.call('DEL', KEYS[1])
return string.sub(value, string.len(expected_prefix) + 1)
"""


def is_issued_scheduling_oauth_state(
    source: SchedulingProviderSource, state: str | None
) -> bool:
    """Fail closed unless ``state`` has the expected form and is unexpired."""

    if not _is_opaque_token(state):
        return False
    assert state is not None
    return bool(get_redis().exists(_redis_key(source, state)))


def issue_scheduling_oauth_state(
    source: SchedulingProviderSource, doctor_id: str
) -> IssuedSchedulingOAuthState:
    """Issue a five-minute state bound to one doctor, provider, and browser cookie."""

    try:
        doctor_ref = TypeID.from_string(doctor_id)
    except TypeIDException:
        raise ValueError("invalid doctor reference") from None
    if doctor_ref.prefix != "doctor":
        raise ValueError("invalid doctor reference")
    encrypted_doctor_id = encrypt_value(str(doctor_ref))
    redis = get_redis()
    for _ in range(_MAX_ISSUE_ATTEMPTS):
        state = secrets.token_urlsafe(32)
        browser_binding = secrets.token_urlsafe(32)
        stored_value = (
            f"{_browser_binding_digest(source, browser_binding)}:{encrypted_doctor_id}"
        )
        if redis.set(
            _redis_key(source, state),
            stored_value,
            nx=True,
            ex=SCHEDULING_OAUTH_STATE_TTL_SECONDS,
        ):
            return IssuedSchedulingOAuthState(state, browser_binding)
    raise RuntimeError("scheduling_oauth_state_issue_failed")


def consume_scheduling_oauth_state(
    source: SchedulingProviderSource, state: str | None, browser_binding: str | None
) -> TypeID | None:
    """Atomically consume a valid state for the browser which initiated it."""

    if not _is_opaque_token(state) or not _is_opaque_token(browser_binding):
        return None
    assert state is not None
    assert browser_binding is not None
    raw_doctor_id = get_redis().eval(
        _ATOMIC_CONSUME_MATCHING_BINDING,
        1,
        _redis_key(source, state),
        _browser_binding_digest(source, browser_binding),
    )
    if raw_doctor_id is None:
        return None
    if isinstance(raw_doctor_id, bytes):
        try:
            encrypted_doctor_id = raw_doctor_id.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(raw_doctor_id, str):
        encrypted_doctor_id = raw_doctor_id
    else:
        return None
    try:
        doctor_id = TypeID.from_string(decrypt_value(encrypted_doctor_id))
    except (DecryptionError, TypeIDException):
        return None
    if doctor_id.prefix != "doctor":
        return None
    return doctor_id
