"""Configured-user/exact ARM-token/cache linkage for a future human launcher.

Pure bounded parsing only: no network, CLI, cache read, human consumer or receipt.
The complete launcher must obtain all inputs through the pinned trusted runtime,
read the protected configured cache, and authenticate a read with this SAME token.
ExpectedIdentity is source configuration, never authority supplied by a plan.
JWT decoding is explicitly NOT independent signature validation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass

CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
ARM_AUDIENCES = frozenset(
    {
        "https://management.azure.com/",
        "https://management.azure.com",
        "https://management.core.windows.net/",
        "https://management.core.windows.net",
    }
)


class IdentityRefused(RuntimeError):
    pass


def require(condition: object) -> None:
    if not condition:
        raise IdentityRefused("snapshot_identity_linkage_refused")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def read_object(raw: bytes, limit: int) -> dict:
    require(type(raw) is bytes and 0 < len(raw) <= limit)
    value = json.loads(
        raw,
        object_pairs_hook=unique_object,
        parse_constant=lambda _value: require(False),
    )
    require(type(value) is dict)
    return value


def canonical_uuid(value: object) -> bool:
    return type(value) is str and str(uuid.UUID(value)) == value


@dataclass(frozen=True)
class ExpectedIdentity:
    subscription: str
    tenant: str
    configured_username_sha256: str


def _bind(
    account: dict,
    token: str,
    protected_cache_bytes: bytes,
    expected: ExpectedIdentity,
    *,
    now_epoch: int,
    minimum_remaining_seconds: int,
) -> dict[str, object]:
    require(type(expected) is ExpectedIdentity)
    require(canonical_uuid(expected.subscription) and canonical_uuid(expected.tenant))
    require(re.fullmatch(r"[0-9a-f]{64}", expected.configured_username_sha256))
    require(type(now_epoch) is int and now_epoch > 0)
    require(
        type(minimum_remaining_seconds) is int
        and 240 <= minimum_remaining_seconds <= 3600
    )
    require(type(account) is dict and type(account.get("user")) is dict)
    user = account["user"]
    require(
        account.get("id") == expected.subscription
        and account.get("tenantId") == expected.tenant
        and account.get("state") == "Enabled"
        and account.get("environmentName") == "AzureCloud"
        and user.get("type") == "user"
        and type(user.get("name")) is str
        and 0 < len(user["name"]) <= 512
        and digest(user["name"]) == expected.configured_username_sha256
    )
    require(type(token) is str and 100 < len(token) <= 65536)
    parts = token.split(".")
    require(
        len(parts) == 3 and all(re.fullmatch(r"[A-Za-z0-9_-]+", item) for item in parts)
    )
    claims = read_object(
        base64.b64decode(
            parts[1] + "=" * (-len(parts[1]) % 4), altchars=b"-_", validate=True
        ),
        32768,
    )
    require(claims.get("tid") == expected.tenant and canonical_uuid(claims.get("oid")))
    require(
        claims.get("iss")
        in {
            f"https://sts.windows.net/{expected.tenant}/",
            f"https://login.microsoftonline.com/{expected.tenant}/v2.0",
        }
        and claims.get("aud") in ARM_AUDIENCES
        and type(claims.get("exp")) is int
        and claims["exp"] > now_epoch + minimum_remaining_seconds
        and type(claims.get("nbf")) is int
        and claims["nbf"] <= now_epoch + 30
    )
    cache = read_object(protected_cache_bytes, 8 * 1024 * 1024)
    require(
        type(cache.get("Account")) is dict and type(cache.get("AccessToken")) is dict
    )
    accounts = [
        item
        for item in cache["Account"].values()
        if type(item) is dict
        and type(item.get("username")) is str
        and item["username"].casefold() == user["name"].casefold()
    ]
    home_ids = {item.get("home_account_id") for item in accounts}
    require(len(home_ids) == 1)
    home_id = next(iter(home_ids))
    if type(home_id) is not str:
        raise IdentityRefused("snapshot_identity_linkage_refused")
    require(0 < len(home_id) < 256)
    access = [
        item
        for item in cache["AccessToken"].values()
        if type(item) is dict and item.get("secret") == token
    ]
    require(
        access
        and all(
            item.get("home_account_id") == home_id
            and item.get("realm") == expected.tenant
            and item.get("credential_type") == "AccessToken"
            and item.get("client_id") == CLI_CLIENT_ID
            for item in access
        )
    )
    return {
        "configured_home_account_linked_to_exact_arm_token": True,
        "configured_username_sha256": expected.configured_username_sha256,
        "current_token_oid_sha256": digest(claims["oid"]),
        "home_account_id_sha256": digest(home_id),
        "exact_arm_token_sha256": digest(token),
        "token_expires_epoch": claims["exp"],
        "observed_at_epoch": now_epoch,
        "jwt_signature_independently_verified": False,
        "authenticated_same_token_read_required": True,
        "historical_apply_oid_attested": False,
        "human_apply_authorized": False,
        "not_a_plan_handoff_or_receipt": True,
    }


def bind_configured_identity(
    account: dict,
    token: str,
    protected_cache_bytes: bytes,
    expected: ExpectedIdentity,
    *,
    now_epoch: int,
    minimum_remaining_seconds: int,
) -> dict[str, object]:
    """All failures have one content-free code, including malformed cache/claims."""
    try:
        return _bind(
            account,
            token,
            protected_cache_bytes,
            expected,
            now_epoch=now_epoch,
            minimum_remaining_seconds=minimum_remaining_seconds,
        )
    except BaseException:
        raise IdentityRefused("snapshot_identity_linkage_refused") from None


if __name__ == "__main__":
    raise SystemExit("snapshot_identity_library_has_no_live_entrypoint")
