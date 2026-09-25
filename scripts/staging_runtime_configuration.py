"""Compare a configuration-preserving staging release without emitting values.

The deployer's workload contracts define its effective configuration. Fingerprint
that complete projection, including every secret, then compare each current
workload with the same resolved values that the deployer will consume. Inputs
containing credentials travel over stdin only; failures are content-free.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any, cast

APPS = {
    "cosmaneura-staging-app",
    "cosmaneura-staging-worker",
    "cosmaneura-staging-scheduler",
}


def require(value: object) -> None:
    if not value:
        raise ValueError("staging_runtime_configuration_refused")


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(type(key) is str and key and key not in result)
        result[key] = value
    return result


def digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def desired(configuration: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    require(type(configuration) is dict and type(contract) is dict)
    require(set(contract) == {"name", "secret_names", "public_names", "args"})
    require(contract["name"] in APPS)
    names: list[str] = []
    for field in ("secret_names", "public_names"):
        entries = contract[field]
        require(type(entries) is list and entries)
        require(
            all(
                type(name) is str and re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
                for name in entries
            )
        )
        names.extend(entries)
    require(len(names) == len(set(names)))
    require(
        type(contract["args"]) is list
        and len(contract["args"]) == 1
        and type(contract["args"][0]) is str
        and contract["args"][0]
    )
    environment = {}
    for name in names:
        value = configuration.get(name)
        require(value is None or type(value) is str)
        # This is the deployer's existing empty-value removal behavior.
        if value:
            environment[name] = value
    return {"environment": environment, "args": contract["args"], "command": []}


def fingerprint(payload: dict[str, Any]) -> str:
    raw_contracts = payload["contracts"]
    require(type(raw_contracts) is list and len(raw_contracts) == len(APPS))
    contracts = cast(list[dict[str, Any]], raw_contracts)
    require({item["name"] for item in contracts} == APPS)
    return digest(
        {item["name"]: desired(payload["configuration"], item) for item in contracts}
    )


def check_workload(payload: dict[str, Any]) -> dict[str, Any]:
    contract, state = payload["contract"], payload["state"]
    target = desired(payload["configuration"], contract)
    name = contract["name"]
    revision = payload["expected_revision"]
    require(
        type(revision) is str
        and re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", revision)
    )
    require(state["name"] == name)
    require(state["latestRevision"] == state["latestReadyRevision"] == revision)
    require(state["readyRevisionCommand"] == target["command"])
    require(state["readyRevisionArgs"] == target["args"])
    public, refs = state["publicConfig"], state["secretRefs"]
    require(type(public) is dict and type(refs) is dict)
    require(not state["legacyPublicEnvNames"])
    require(set(public) <= set(contract["public_names"]))
    require(set(refs) <= set(contract["secret_names"]))
    actual = dict(public)
    secrets = payload["secrets"]
    require(type(secrets) is list)
    values = unique([(item["name"], item.get("value")) for item in secrets])
    for key, reference in cast(dict[str, str], refs).items():
        require(reference == key.lower().replace("_", "-"))
        value = values.get(reference)
        require(type(value) is str and value)
        actual[key] = value
    require(actual == target["environment"])
    return {"name": name, "preserved": True, "runtime_sha256": digest(target)}


def main(argv: list[str]) -> None:
    require(len(argv) == 1)
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024)
    payload = json.loads(raw, object_pairs_hook=unique)
    require(type(payload) is dict)
    if argv[0] == "--fingerprint":
        print(fingerprint(payload))
    elif argv[0] == "--check-workload":
        print(json.dumps(check_workload(payload), sort_keys=True))
    else:
        require(False)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit("staging_runtime_configuration_refused") from None
