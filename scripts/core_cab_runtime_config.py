"""Offline release validation for the API-only Core CAB channel; emits no values."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

PUBLIC_DEFAULTS = {
    "CORE_CAB_AUTHORIZE_ENABLED": "false",
    "CORE_CAB_AUTHORIZE_TENANT_ID": "",
    "CORE_CAB_AUTHORIZE_AUDIENCE": "",
    "CORE_CAB_AUTHORIZE_CALLER_ID": "",
    "CORE_CAB_BRIDGE_ENABLED": "false",
    "CORE_CAB_ORIGIN": "",
    "CORE_CAB_AUDIENCE": "",
    "CORE_CAB_MANAGED_IDENTITY_CLIENT_ID": "",
}
POLICY_NAME = "BILLING_SOURCE_POLICY_JSON"
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def require(condition):
    if not condition:
        raise ValueError("invalid_core_cab_runtime_configuration")


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def object_json(raw):
    value = json.loads(
        raw, object_pairs_hook=unique, parse_constant=lambda _: require(False)
    )
    require(type(value) is dict)
    return value


def origin(value):
    try:
        p = urlsplit(value)
        return (
            type(value) is str
            and p.scheme == "https"
            and bool(p.hostname)
            and p.port in (None, 443)
            and not p.path
            and not p.query
            and not p.fragment
            and not p.username
            and not p.password
            and not any(c.isspace() or ord(c) < 32 for c in value)
        )
    except (ValueError, TypeError):
        return False


def identifier(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def validate(values, *, now=None):
    require(type(values) is dict)
    config = {key: values.get(key, default) for key, default in PUBLIC_DEFAULTS.items()}
    require(all(type(v) is str for v in config.values()))
    for key in ("CORE_CAB_AUTHORIZE_ENABLED", "CORE_CAB_BRIDGE_ENABLED"):
        require(config[key] in {"true", "false"})
    ids = (
        "CORE_CAB_AUTHORIZE_TENANT_ID",
        "CORE_CAB_AUTHORIZE_AUDIENCE",
        "CORE_CAB_AUTHORIZE_CALLER_ID",
        "CORE_CAB_AUDIENCE",
        "CORE_CAB_MANAGED_IDENTITY_CLIENT_ID",
    )
    require(all(not config[key] or identifier(config[key]) for key in ids))
    require(not config["CORE_CAB_ORIGIN"] or origin(config["CORE_CAB_ORIGIN"]))
    if config["CORE_CAB_AUTHORIZE_ENABLED"] == "true":
        require(all(config[key] for key in ids[:3]))
    if config["CORE_CAB_BRIDGE_ENABLED"] == "true":
        require(config["CORE_CAB_AUTHORIZE_ENABLED"] == "true")
        require(all(config[key] for key in ids[3:]) and config["CORE_CAB_ORIGIN"])
    raw = values.get(POLICY_NAME, "")
    require(type(raw) is str and len(raw.encode("utf-8")) <= 131072)
    require(not raw.endswith("\n") and "\x00" not in raw)
    if not raw:
        return
    policy = object_json(raw)
    require(set(policy) == {"schemaVersion", "enabled", "clerkIssuer", "admissions"})
    require(policy["schemaVersion"] == "core.billing-source-policy.v1")
    require(type(policy["enabled"]) is bool and type(policy["clerkIssuer"]) is str)
    require(type(policy["admissions"]) is list and len(policy["admissions"]) <= 128)
    if policy["enabled"]:
        require(origin(policy["clerkIssuer"]))
        require(policy["clerkIssuer"] == values.get("AUTHORITY_CLERK_ISSUER"))
    seen = set()
    now = now or datetime.now(timezone.utc)
    fields = {
        "admissionId",
        "practiceId",
        "doctorId",
        "appointmentId",
        "patientId",
        "noteId",
        "contentSha256",
        "dataClass",
        "reviewRef",
        "expiresAt",
    }
    for row in policy["admissions"]:
        require(type(row) is dict and set(row) == fields)
        require(all(type(row[key]) is str for key in fields))
        require(
            all(
                REF.fullmatch(row[key])
                for key in fields - {"expiresAt", "contentSha256", "dataClass"}
            )
        )
        require(DIGEST.fullmatch(row["contentSha256"]))
        require(row["dataClass"] in {"synthetic", "deidentified"})
        require(row["noteId"] not in seen)
        seen.add(row["noteId"])
        expiry = datetime.fromisoformat(row["expiresAt"].replace("Z", "+00:00"))
        require(expiry.tzinfo is not None)
        if policy["enabled"]:
            require(expiry > now)


def main():
    try:
        require(sys.argv[1:] in ([], ["--stdin"]))
        if sys.argv[1:]:
            raw = sys.stdin.buffer.read(2_000_001)
            require(len(raw) <= 2_000_000)
            values = object_json(raw)
        else:
            values = dict(os.environ)
        validate(values)
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
        print("Core CAB runtime configuration is invalid.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
