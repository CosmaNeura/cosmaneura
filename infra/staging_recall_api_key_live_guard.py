"""Admit only the existing common Recall key on the three owned staging apps."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
TENANT = "d51754d0-bba5-4810-820e-afac542844d6"
GROUP = "cosmaneura-staging"
API_ORIGIN = "https://staging-app.cosmaneura.com"
RECALL_BASE = "https://us-west-2.recall.ai/api/v1"
INPUT = "RECALL_API_KEY_INPUT"
SECRET = "recall-api-key"
WORKLOADS = (
    ("cosmaneura-staging-app", "cosmaneura-staging-app-web"),
    ("cosmaneura-staging-worker", "cosmaneura-staging-app-worker"),
    ("cosmaneura-staging-scheduler", "cosmaneura-staging-app-scheduler"),
)


class PreservationRefused(RuntimeError):
    """A content-free failure; never include credentials or provider output."""


def require(value: object) -> None:
    if not value:
        raise PreservationRefused("staging_recall_key_preservation_refused")


def key_hash(value: object) -> str:
    require(isinstance(value, str))
    if not isinstance(value, str):
        raise PreservationRefused("staging_recall_key_preservation_refused")
    require(0 < len(value.encode()) <= 4096)
    require(not any(char in value for char in ("\x00", "\r", "\n")))
    return hashlib.sha256(value.encode()).hexdigest()


def validate_context(environment: Mapping[str, str]) -> str:
    require(environment.get("PYTHON_ENV") == "staging")
    require(environment.get("API_BASE_URL") == API_ORIGIN)
    require(environment.get("RECALL_API_BASE") == RECALL_BASE)
    return key_hash(environment.get(INPUT))


def _run_az(arguments: tuple[str, ...]) -> str:
    # The app's resolved keys/input are not capabilities this read transport
    # needs. Use only the existing CLI profile and minimal process environment.
    environment = {
        name: os.environ[name] for name in ("PATH", "HOME") if name in os.environ
    }
    environment.update({"LANG": "C", "LC_ALL": "C"})
    try:
        result = subprocess.run(
            [
                "az",
                *arguments,
                "--subscription",
                SUBSCRIPTION,
                "--only-show-errors",
                "--output",
                "json",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=environment,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise PreservationRefused("staging_recall_key_preservation_refused") from None
    require(result.returncode == 0 and 0 < len(result.stdout) <= 65536)
    return result.stdout


@dataclass(frozen=True)
class Snapshot:
    name: str
    ready_revision: str
    container: str
    api_origin: str
    recall_base: str
    key_sha256: str
    api_traffic_sha256: str | None


def snapshot(
    name: str,
    container: str,
    read: Callable[[tuple[str, ...]], Any],
) -> Snapshot:
    require((name, container) in WORKLOADS)
    info = read(
        (
            "containerapp",
            "show",
            "--name",
            name,
            "--resource-group",
            GROUP,
            "--query",
            "{name:name,latest:properties.latestRevisionName,ready:properties.latestReadyRevisionName,mode:properties.configuration.activeRevisionsMode,traffic:properties.configuration.ingress.traffic}",
        )
    )
    require(isinstance(info, dict))
    ready = info.get("ready")
    require(
        isinstance(ready, str)
        and re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", ready)
    )
    require(
        info.get("name") == name
        and info.get("latest") == ready
        and info.get("mode") == "Single"
    )
    traffic_hash = None
    if name == WORKLOADS[0][0]:
        traffic = info.get("traffic")
        require(isinstance(traffic, list))
        positive = [
            item
            for item in traffic
            if isinstance(item, dict) and item.get("weight", 0) > 0
        ]
        require(len(positive) == 1 and positive[0].get("weight") == 100)
        require(
            positive[0].get("latestRevision") is True
            or positive[0].get("revisionName") == ready
        )
        traffic_hash = hashlib.sha256(
            json.dumps(traffic, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    revision = read(
        (
            "containerapp",
            "revision",
            "show",
            "--name",
            name,
            "--resource-group",
            GROUP,
            "--revision",
            ready,
            "--query",
            "{health:properties.healthState,provisioning:properties.provisioningState,running:properties.runningState,containers:properties.template.containers[].{name:name,entries:env[?name=='RECALL_API_KEY'||name=='RECALL_API_BASE'||name=='API_BASE_URL'].{name:name,value:value,secretRef:secretRef}}}",
        )
    )
    require(isinstance(revision, dict))
    require(
        revision.get("health") == "Healthy"
        and revision.get("provisioning") == "Provisioned"
    )
    require(revision.get("running") in {"Running", "RunningAtMaxScale"})
    containers = revision.get("containers")
    require(
        isinstance(containers, list)
        and len(containers) == 1
        and containers[0].get("name") == container
    )
    entries = containers[0].get("entries")
    require(isinstance(entries, list) and len(entries) == 3)
    values = {}
    for entry in entries:
        require(isinstance(entry, dict))
        variable = entry.get("name")
        require(
            variable in {"RECALL_API_KEY", "RECALL_API_BASE", "API_BASE_URL"}
            and variable not in values
        )
        values[variable] = entry
    require(
        values["RECALL_API_KEY"].get("secretRef") == SECRET
        and values["RECALL_API_KEY"].get("value") in {None, ""}
    )
    for variable, expected in (
        ("API_BASE_URL", API_ORIGIN),
        ("RECALL_API_BASE", RECALL_BASE),
    ):
        require(
            values[variable].get("secretRef") is None
            and values[variable].get("value") == expected
        )
    secrets = read(
        (
            "containerapp",
            "secret",
            "list",
            "--name",
            name,
            "--resource-group",
            GROUP,
            "--show-values",
            "--query",
            "[?name=='recall-api-key'].{name:name,value:value}",
        )
    )
    require(
        isinstance(secrets, list) and len(secrets) == 1 and isinstance(secrets[0], dict)
    )
    require(set(secrets[0]) == {"name", "value"} and secrets[0]["name"] == SECRET)
    digest = key_hash(secrets[0]["value"])
    del secrets
    return Snapshot(
        name, ready, container, API_ORIGIN, RECALL_BASE, digest, traffic_hash
    )


def verify_preservation(
    environment: Mapping[str, str],
    *,
    runner: Callable[[tuple[str, ...]], str] = _run_az,
) -> dict[str, object]:
    input_hash = validate_context(environment)
    deadline = time.monotonic() + 60

    def read(arguments: tuple[str, ...]) -> Any:
        require(time.monotonic() < deadline)
        try:
            value = json.loads(runner(arguments))
        except (
            OSError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            UnicodeError,
        ):
            raise PreservationRefused(
                "staging_recall_key_preservation_refused"
            ) from None
        require(time.monotonic() < deadline)
        return value

    def account() -> None:
        require(
            read(
                ("account", "show", "--query", "{id:id,tenantId:tenantId,state:state}")
            )
            == {"id": SUBSCRIPTION, "tenantId": TENANT, "state": "Enabled"}
        )

    def collect() -> tuple[Snapshot, ...]:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(snapshot, name, container, read)
                for name, container in WORKLOADS
            ]
            return tuple(future.result() for future in futures)

    account()
    before = collect()
    require(all(item.key_sha256 == input_hash for item in before))
    after = collect()
    account()
    require(before == after)
    return {
        "result": "verified",
        "environment": "staging",
        "input_key_sha256": input_hash,
        "workloads": [asdict(item) for item in after],
        "all_three_keys_equal_input": True,
        "before_after_owned_state_equal": True,
        "credential_or_configuration_mutated": False,
    }


def main(arguments: list[str]) -> int:
    try:
        require(not arguments)
        evidence = verify_preservation(os.environ)
    except (PreservationRefused, TypeError, KeyError, ValueError, AttributeError):
        print(
            json.dumps({"code": "staging_recall_key_preservation_failed"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
