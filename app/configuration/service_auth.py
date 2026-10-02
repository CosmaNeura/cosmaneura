import json
import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import httpx
import jwt
from decouple import config

AGENT_API_ACCESS_ROLE = "AgentApi.Access"
SCRIBE_TRANSCRIPT_WRITE_ROLE = "Scribe.Transcript.Write"


@dataclass(frozen=True)
class EntraServiceAuthSettings:
    enabled: bool
    tenant_id: str
    audience: str
    scribe_client_id: str = ""
    scribe_reconciler_client_id: str = ""
    require_nbf: bool = False

    @property
    def required_role(self) -> str:
        return SCRIBE_TRANSCRIPT_WRITE_ROLE

    @property
    def log_namespace(self) -> str:
        return "scribe_handoff"

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def allowed_client_ids(self) -> frozenset[str]:
        """Return the canonical, deployment-owned physical caller allowlist."""

        try:
            return frozenset(
                {
                    str(UUID(self.scribe_client_id)),
                    str(UUID(self.scribe_reconciler_client_id)),
                }
            )
        except (ValueError, TypeError, AttributeError):
            return frozenset()

    def is_complete(self) -> bool:
        try:
            UUID(self.tenant_id)
            UUID(self.audience)
            UUID(self.scribe_client_id)
            UUID(self.scribe_reconciler_client_id)
        except (ValueError, TypeError, AttributeError):
            return False
        return len(self.allowed_client_ids) == 2


@dataclass(frozen=True)
class EntraServiceAuthPolicy:
    """Reusable caller/role policy retained for focused unit construction."""

    settings: EntraServiceAuthSettings
    allowed_client_ids: frozenset[str]
    required_role: str
    required_client_count: int = 1
    log_namespace: str = "service_auth"

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def tenant_id(self) -> str:
        return self.settings.tenant_id

    @property
    def audience(self) -> str:
        return self.settings.audience

    @property
    def issuer(self) -> str:
        return self.settings.issuer

    @property
    def jwks_url(self) -> str:
        return self.settings.jwks_url

    @property
    def require_nbf(self) -> bool:
        return self.settings.require_nbf

    def is_complete(self) -> bool:
        try:
            UUID(self.tenant_id)
            UUID(self.audience)
            canonical_ids = frozenset(
                str(UUID(client_id)) for client_id in self.allowed_client_ids
            )
        except (ValueError, TypeError, AttributeError):
            return False
        return (
            canonical_ids == self.allowed_client_ids
            and len(canonical_ids) == self.required_client_count
            and bool(self.required_role)
        )


@dataclass(frozen=True)
class EntraAgentAuthSettings:
    """Fail-closed policy for the provider Sovereign staging workload."""

    enabled: bool
    tenant_id: str
    audience: str
    sovereign_client_ids: frozenset[str]
    required_client_count: int = 1
    require_nbf: bool = True

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def allowed_client_ids(self) -> frozenset[str]:
        return self.sovereign_client_ids

    @property
    def required_role(self) -> str:
        return AGENT_API_ACCESS_ROLE

    @property
    def log_namespace(self) -> str:
        return "agent_api"

    def is_complete(self) -> bool:
        try:
            UUID(self.tenant_id)
            UUID(self.audience)
            canonical_ids = frozenset(
                str(UUID(client_id)) for client_id in self.sovereign_client_ids
            )
        except (ValueError, TypeError, AttributeError):
            return False
        return (
            len(canonical_ids) == self.required_client_count
            and canonical_ids == self.sovereign_client_ids
        )


def _client_id_allowlist(value: str) -> frozenset[str]:
    """Canonicalize UUIDs while retaining invalid input so startup fails closed."""

    client_ids: set[str] = set()
    for candidate in value.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            client_ids.add(str(UUID(candidate)))
        except (ValueError, TypeError, AttributeError):
            client_ids.add(candidate)
    return frozenset(client_ids)


SERVICE_AUTH_SETTINGS = EntraServiceAuthSettings(
    enabled=config("SCRIBE_HANDOFF_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("SCRIBE_API_AUDIENCE", default="", cast=str),
    scribe_client_id=config("SCRIBE_CALLER_CLIENT_ID", default="", cast=str),
    scribe_reconciler_client_id=config(
        "SCRIBE_RECONCILER_CALLER_CLIENT_ID", default="", cast=str
    ),
)


# Optional additional receiver, with the existing one-caller policy unchanged
# when unset. This deployment-owned value is not accepted from request data.
_AGENT_SCRIBE_RECEIVER = config(
    "AGENT_API_SCRIBE_RECEIVER_CLIENT_ID", default="", cast=str
).strip()
_AGENT_COMPANION_RECEIVER = config(
    "AGENT_API_COMPANION_CLIENT_ID", default="", cast=str
).strip()
_AGENT_SOVEREIGN_IDS = _client_id_allowlist(
    config("AGENT_API_SOVEREIGN_CLIENT_IDS", default="", cast=str)
)
_AGENT_PROVIDER_IDS = _AGENT_SOVEREIGN_IDS
_AGENT_CAPTURE_IDS: frozenset[str] = frozenset()
_AGENT_COMPANION_IDS: frozenset[str] = frozenset()
_AGENT_REQUIRED_CLIENT_COUNT = 1
if _AGENT_SCRIBE_RECEIVER:
    try:
        _receiver_id = UUID(_AGENT_SCRIBE_RECEIVER)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("agent_api_receiver_configuration_invalid") from None
    if (
        not _receiver_id.int
        or len(_AGENT_SOVEREIGN_IDS) != 1
        or str(_receiver_id) in _AGENT_SOVEREIGN_IDS
    ):
        raise ValueError("agent_api_receiver_configuration_invalid")
    _AGENT_SOVEREIGN_IDS = _AGENT_SOVEREIGN_IDS | {str(_receiver_id)}
    _AGENT_CAPTURE_IDS = frozenset({str(_receiver_id)})
    _AGENT_REQUIRED_CLIENT_COUNT = 2

if _AGENT_COMPANION_RECEIVER:
    try:
        _companion_id = UUID(_AGENT_COMPANION_RECEIVER)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("agent_api_companion_configuration_invalid") from None
    if (
        not _companion_id.int
        or len(_AGENT_PROVIDER_IDS) != 1
        or str(_companion_id) in _AGENT_SOVEREIGN_IDS
    ):
        raise ValueError("agent_api_companion_configuration_invalid")
    # The companion shares capture/context protocols and may separately opt
    # into its brief receiver. Never grant it the generic Sovereign tools.
    _AGENT_SOVEREIGN_IDS = _AGENT_SOVEREIGN_IDS | {str(_companion_id)}
    _AGENT_CAPTURE_IDS = _AGENT_CAPTURE_IDS | {str(_companion_id)}
    _AGENT_COMPANION_IDS = frozenset({str(_companion_id)})
    _AGENT_REQUIRED_CLIENT_COUNT += 1

AGENT_API_AUTH_SETTINGS = EntraAgentAuthSettings(
    enabled=config("AGENT_API_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("AGENT_API_AUDIENCE", default="", cast=str),
    sovereign_client_ids=_AGENT_SOVEREIGN_IDS,
    required_client_count=_AGENT_REQUIRED_CLIENT_COUNT,
)

# Context resolution and conversation receipts are shared protocol operations.
# Feature endpoints use separate callers even when both hold AgentApi.Access.
SOVEREIGN_TOOL_AUTH_SETTINGS = replace(
    AGENT_API_AUTH_SETTINGS,
    sovereign_client_ids=_AGENT_PROVIDER_IDS,
    required_client_count=1,
)
SCRIBE_CAPTURE_AUTH_SETTINGS = replace(
    AGENT_API_AUTH_SETTINGS,
    sovereign_client_ids=_AGENT_CAPTURE_IDS,
    required_client_count=max(1, len(_AGENT_CAPTURE_IDS)),
)

# Reuse the explicitly configured companion identity, but require an additional
# deployment permission for the brief-only receiver. Missing identity fails
# closed; neither Sovereign nor Scribe is a fallback caller.
COMPANION_SESSION_PREP_AUTH_SETTINGS = replace(
    AGENT_API_AUTH_SETTINGS,
    enabled=AGENT_API_AUTH_SETTINGS.enabled
    and config("COMPANION_SESSION_PREP_ENABLED", default=False, cast=bool),
    sovereign_client_ids=_AGENT_COMPANION_IDS,
    required_client_count=1,
)

# A distinct dark receiver permission, not a generic tool or Session Prep grant.
COMPANION_VISIT_PREP_AUTH_SETTINGS = replace(
    AGENT_API_AUTH_SETTINGS,
    enabled=AGENT_API_AUTH_SETTINGS.enabled
    and config("COMPANION_VISIT_PREP_ENABLED", default=False, cast=bool),
    sovereign_client_ids=_AGENT_COMPANION_IDS,
    required_client_count=1,
)


class CompanionAuthenticationRejected(Exception):
    """Value-free failure; never retain an SDK exception, token or claim set."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__("Companion authentication unavailable.")


@dataclass(frozen=True)
class VerifiedCredentialWindow:
    """Verified JWT bounds retained for authorization against database time."""

    not_before: int
    issued_at: int
    expires_at: int

    @classmethod
    def from_verified_claims(cls, claims: dict[str, Any]):
        bounds = [claims.get(name) for name in ("nbf", "iat", "exp")]
        if not all(
            type(value) is int and 0 <= value <= 253402300799 for value in bounds
        ):
            raise CompanionAuthenticationRejected(401)
        window = cls(*(cast(int, value) for value in bounds))
        if max(window.not_before, window.issued_at) >= window.expires_at:
            raise CompanionAuthenticationRejected(401)
        return window

    def valid_at(self, checked_at: datetime) -> bool:
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            return False
        now = checked_at.timestamp()
        return max(self.not_before, self.issued_at) <= now < self.expires_at


@dataclass(frozen=True)
class VisitPrepWorkloadPrincipal:
    client_id: str
    window: VerifiedCredentialWindow


def companion_verification_timeout(remaining_seconds: float) -> float:
    if (
        isinstance(remaining_seconds, bool)
        or not isinstance(remaining_seconds, (int, float))
        or not math.isfinite(remaining_seconds)
        or remaining_seconds <= 0
    ):
        raise CompanionAuthenticationRejected(504)
    return min(4.0, remaining_seconds)


def _unique_jwks_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous_jwks")
        result[key] = value
    return result


def companion_public_jwks(url: str, timeout: float) -> dict[str, Any]:
    """One bounded public-key fetch; no proxy environment, redirect or retry."""
    with httpx.Client(
        trust_env=False, follow_redirects=False, timeout=timeout
    ) as client:
        with client.stream(
            "GET", url, headers={"Accept-Encoding": "identity"}
        ) as response:
            if (
                response.status_code != 200
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise CompanionAuthenticationRejected(503)
            body = bytearray()
            for chunk in response.iter_raw():
                body.extend(chunk)
                if len(body) > 65536:
                    raise CompanionAuthenticationRejected(503)
    result = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_jwks_object)
    if not isinstance(result, dict) or not isinstance(result.get("keys"), list):
        raise CompanionAuthenticationRejected(503)
    return result


def companion_signing_key(token: str, keys: list[dict[str, Any]]):
    """Unverified header selects a key only from the configured authority."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise CompanionAuthenticationRejected(401) from None
    kid = header.get("kid")
    if (
        header.get("alg") != "RS256"
        or not isinstance(kid, str)
        or not 1 <= len(kid) <= 200
        or header.get("crit")
        or not isinstance(keys, list)
        or len(keys) > 32
    ):
        raise CompanionAuthenticationRejected(401)
    matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid]
    if len(matches) != 1:
        raise CompanionAuthenticationRejected(401)
    key = matches[0]
    if (
        key.get("kty") != "RSA"
        or key.get("use") not in {None, "sig"}
        or key.get("alg") not in {None, "RS256"}
    ):
        raise CompanionAuthenticationRejected(401)
    return jwt.PyJWK.from_dict(key, algorithm="RS256").key


def verify_visit_prep_workload(
    token: str,
    *,
    remaining_seconds: float,
    settings: EntraAgentAuthSettings | None = None,
) -> VisitPrepWorkloadPrincipal:
    """Dedicated app-only admission; domain code receives no raw claims."""
    policy = settings or COMPANION_VISIT_PREP_AUTH_SETTINGS
    if (
        not policy.enabled
        or not policy.is_complete()
        or policy.required_client_count != 1
        or any(
            UUID(value).int == 0
            for value in (*policy.allowed_client_ids, policy.tenant_id, policy.audience)
        )
    ):
        raise CompanionAuthenticationRejected(503)
    timeout = companion_verification_timeout(remaining_seconds)
    # Reject malformed/non-JWT credentials without a network request.
    try:
        if len(token) > 8192 or not token.isascii() or any(c.isspace() for c in token):
            raise CompanionAuthenticationRejected(401)
        jwt.get_unverified_header(token)
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError):
        raise CompanionAuthenticationRejected(401) from None
    try:
        jwks = companion_public_jwks(policy.jwks_url, timeout)
        key = companion_signing_key(token, jwks["keys"])
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=policy.audience,
            issuer=policy.issuer,
            options={"require": ["aud", "iss", "sub", "tid", "nbf", "iat", "exp"]},
        )
        window = VerifiedCredentialWindow.from_verified_claims(claims)
        if (
            claims.get("tid") != policy.tenant_id
            or claims.get("aud") != policy.audience
        ):
            raise CompanionAuthenticationRejected(401)
        try:
            client_id = str(UUID(claims.get("azp")))
        except (ValueError, TypeError, AttributeError):
            raise CompanionAuthenticationRejected(403) from None
        if (
            claims.get("idtyp") != "app"
            or "scp" in claims
            or client_id not in policy.allowed_client_ids
            or not isinstance(claims.get("roles"), list)
            or not all(isinstance(role, str) for role in claims["roles"])
            or AGENT_API_ACCESS_ROLE not in claims["roles"]
        ):
            raise CompanionAuthenticationRejected(403)
        return VisitPrepWorkloadPrincipal(client_id=client_id, window=window)
    except CompanionAuthenticationRejected:
        raise
    except jwt.PyJWTError:
        raise CompanionAuthenticationRejected(401) from None
    except Exception:
        raise CompanionAuthenticationRejected(503) from None
