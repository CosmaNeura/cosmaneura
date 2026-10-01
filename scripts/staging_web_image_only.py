#!/usr/bin/env python3
"""Reviewed, hash-bound Core web image update; preserve all runtime configuration.

The command is a separate founder-delegated release step, not build authority.
It never publishes, migrates, resolves deployment configuration, writes secrets,
or updates worker/scheduler. Failed mutation calls require observation/recovery.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pty
import re
import resource
import runpy
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
WEB = "cosmaneura-staging-app"
APPS = {WEB, "cosmaneura-staging-worker", "cosmaneura-staging-scheduler"}
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
GROUP = "cosmaneura-staging"
AZ = "/opt/homebrew/bin/az"
AZ_PYTHON = "/opt/homebrew/Cellar/azure-cli/2.89.1/libexec/bin/python"
HELPERS = {
    "staging_web_image_only.py",
    "staging_azure_cli_bound.py",
    "staging_release_contract.py",
}


def require(value: object) -> None:
    if not value:
        raise ValueError("staging_web_image_only_refused")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: object) -> str:
    return sha(canonical(value))


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def read_bound(filename: str, expected: str) -> Any:
    require(re.fullmatch(r"[0-9a-f]{64}", expected))
    path = Path(filename)
    require(
        path.is_absolute()
        and path.resolve() == path
        and path.is_relative_to("/private/tmp")
    )
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        require(before.st_uid == os.geteuid() and not before.st_mode & 0o022)
        raw = stream.read(65537)
        after = os.fstat(stream.fileno())
    require(0 < len(raw) <= 65536 and sha(raw) == expected)
    fields = (
        "st_dev",
        "st_ino",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_size",
        "st_mode",
        "st_nlink",
    )
    require(
        all(
            getattr(before, f) == getattr(after, f) == getattr(path.stat(), f)
            for f in fields
        )
    )
    return json.loads(raw, object_pairs_hook=unique)


def bound(spec: dict[str, str]) -> Any:
    require(type(spec) is dict and set(spec) == {"path", "sha256"})
    return read_bound(spec["path"], spec["sha256"])


def write_once(path: Path, value: object) -> None:
    require(
        path.is_absolute()
        and path.resolve() == path
        and path.is_relative_to("/private/tmp")
    )
    require(
        path.parent.stat().st_uid == os.geteuid()
        and not path.parent.stat().st_mode & 0o022
    )
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "wb"
    ) as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(
    argv: list[str], *, data: bytes | None = None, terminal: bool = False
) -> bytes:
    master = slave = None
    if terminal:
        master, slave = pty.openpty()
    try:
        result = subprocess.run(
            argv, input=data, stdin=slave, capture_output=True, timeout=100
        )
    finally:
        if slave is not None:
            os.close(slave)
            os.close(master)  # type: ignore[arg-type]
    require(
        result.returncode == 0
        and len(result.stdout) <= 1024**2
        and len(result.stderr) <= 1024**2
    )
    return result.stdout


def verify_checkout(build: dict[str, Any]) -> None:
    require(
        command(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "HEAD"]).decode().strip()
        == build["controller_sha"]
    )
    require(command(["/usr/bin/git", "-C", str(ROOT), "status", "--porcelain"]) == b"")


def source_verify(spec: dict[str, str], build: dict[str, Any]) -> None:
    verify_checkout(build)
    result = json.loads(
        command(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(ROOT / "scripts/staging_build_controller.py"),
                "--verify-source",
                spec["path"],
                spec["sha256"],
            ]
        )
    )
    require(
        result["authenticated_source_checked"] is True
        and result["build_started"] is False
    )
    require(
        result["plan_sha256"] == spec["sha256"]
        and result["candidate_sha"] == build["candidate_sha"]
    )


def az(name: str, prefix: list[str], *args: str) -> Any:
    require(name in APPS)
    token = command(
        [
            AZ,
            "account",
            "get-access-token",
            "--subscription",
            SUBSCRIPTION,
            "--resource",
            "https://management.azure.com/",
            "--output",
            "json",
            "--only-show-errors",
        ]
    )
    argv = [
        "/bin/bash",
        "-c",
        'exec "$@" 3<&0',
        "bound-azure",
        AZ_PYTHON,
        "-I",
        "-B",
        str(ROOT / "scripts/staging_azure_cli_bound.py"),
        "--runtime-operation",
        "web-image-only",
        *prefix,
        "--name",
        name,
        "--resource-group",
        GROUP,
        *args,
        "--subscription",
        SUBSCRIPTION,
        "--only-show-errors",
        "--output",
        "json",
    ]
    # For exec, token stdin becomes fd3; its independent stdin tty is fd4.
    if prefix == ["containerapp", "exec"]:
        master, slave = pty.openpty()
        try:
            process = subprocess.run(
                argv[:1] + ["-c", f'exec "$@" 3<&0 0<&{slave}', *argv[3:]],
                input=token,
                pass_fds=(slave,),
                capture_output=True,
                timeout=100,
            )
        finally:
            os.close(slave)
            os.close(master)
        require(
            process.returncode == 0
            and len(process.stdout) <= 65536
            and len(process.stderr) <= 65536
        )
        return process.stdout
    return json.loads(command(argv, data=token))


def image(template: dict[str, Any]) -> str:
    require(len(template["containers"]) == 1)
    return template["containers"][0]["image"]


def template_projection(
    template: dict[str, Any], *, without_image: bool = False
) -> dict[str, Any]:
    value = copy.deepcopy(template)
    value.pop("revisionSuffix", None)
    for container in value["containers"]:
        container.pop("imageType", None)
        container.get("resources", {}).pop("ephemeralStorage", None)
        for entry in container.get("env", []):
            if entry.get("secretRef"):
                entry.pop("value", None)
        if without_image:
            container.pop("image", None)
    return value


def observe(
    name: str, *, recovery: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = az(name, ["containerapp", "show"])
    app = raw["properties"]
    require(app["configuration"]["activeRevisionsMode"] == "Single")
    require(app["provisioningState"] == "Succeeded")
    latest, ready = app["latestRevisionName"], app["latestReadyRevisionName"]
    require(re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", latest))
    require(re.fullmatch(re.escape(name) + r"--[a-z0-9-]+", ready))
    require(recovery or latest == ready)
    revision = az(name, ["containerapp", "revision", "show"], "--revision", ready)[
        "properties"
    ]
    require(
        revision["healthState"] == "Healthy"
        and revision["provisioningState"] == "Provisioned"
    )
    template = app["template"]
    require(
        recovery
        or template_projection(template)["containers"]
        == template_projection(revision["template"])["containers"]
    )
    container = template["containers"][0]
    env: dict[str, str] = {}
    secrets = az(name, ["containerapp", "secret", "list"], "--show-values")
    values = {entry["name"]: entry.get("value") for entry in secrets}
    require(len(values) == len(secrets))
    for entry in container.get("env", []):
        require(entry["name"] not in env)
        value = (
            values.get(entry["secretRef"])
            if entry.get("secretRef")
            else entry.get("value")
        )
        require(type(value) is str)
        env[entry["name"]] = cast(str, value)
    effective = {
        "environment": env,
        "args": container.get("args") or [],
        "command": container.get("command") or [],
    }
    proof = {
        "name": name,
        "revision": latest,
        "ready_revision": ready,
        "image": image(template),
        "ready_image": image(revision["template"]),
        "identity_sha256": digest(raw.get("identity")),
        "configuration_sha256": digest(app["configuration"]),
        "template_sha256": digest(template_projection(template)),
        "template_without_image_sha256": digest(
            template_projection(template, without_image=True)
        ),
        "ready_template_without_image_sha256": digest(
            template_projection(revision["template"], without_image=True)
        ),
        "effective_runtime_sha256": digest(effective),
    }
    return proof, app


def state_equal(
    current: dict[str, Any], prior: dict[str, Any], *, new_image: str | None = None
) -> None:
    if new_image is None:
        require(current == prior)
    else:
        require(current["name"] == prior["name"] == WEB)
        require(current["image"] == current["ready_image"] == new_image)
        require(current["revision"] == current["ready_revision"])
        for key in (
            "identity_sha256",
            "configuration_sha256",
            "template_without_image_sha256",
            "ready_template_without_image_sha256",
            "effective_runtime_sha256",
        ):
            require(current[key] == prior[key])


def schema(app: dict[str, Any]) -> str:
    revision = app["latestReadyRevisionName"]
    container = app["template"]["containers"][0]["name"]
    require(container == "cosmaneura-staging-app-web")
    replicas = az(WEB, ["containerapp", "replica", "list"], "--revision", revision)
    matches = sorted(
        row["name"]
        for row in replicas
        if any(
            item["name"] == container and item.get("ready") is True
            for item in row["properties"]["containers"]
        )
    )
    require(matches)
    guard = runpy.run_path(str(ROOT / "scripts/staging_azure_cli_bound.py"))
    raw = az(
        WEB,
        ["containerapp", "exec"],
        "--revision",
        revision,
        "--replica",
        matches[0],
        "--container",
        container,
        "--command",
        guard["WEB_SCHEMA_PROBE_COMMAND"],
    )
    markers = re.findall(rb"(?m)^STAGING_SCHEMA_REVISION=([0-9a-f]{12})\r?$", raw)
    require(len(markers) == 1)
    return markers[0].decode()


def snapshot(build: dict[str, Any], spec: dict[str, str]) -> dict[str, Any]:
    require(build["version"] == 2 and build["release"]["migration_revisions"] == [])
    require(build["release"]["schema_before"] == build["release"]["schema_after"])
    source_verify(spec, build)
    rows = {name: observe(name) for name in sorted(APPS)}
    observed = schema(rows[WEB][1])
    require(observed == build["release"]["schema_before"])
    for name, (proof, _) in rows.items():
        state_equal(observe(name)[0], proof)
        require(proof["revision"] == build["release"]["recovery"][name]["revision"])
        require(proof["image"] == build["release"]["recovery"][name]["image"])
    return {
        "version": 1,
        "build_plan_sha256": spec["sha256"],
        "schema": observed,
        "observed_at": datetime.now(UTC).isoformat(),
        "workloads": {n: v[0] for n, v in rows.items()},
        "clinical_rows_read": False,
        "secret_values_retained": False,
    }


def prepare(
    plan: dict[str, Any], *, recovery: bool = False
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    require(
        set(plan)
        == {
            "version",
            "action",
            "actor",
            "created_at",
            "expires_at",
            "build_plan",
            "publication",
            "baseline",
            "helper_sha256s",
            "intent_path",
        }
    )
    require(
        plan["version"] == 1
        and plan["action"] == "staging_core_web_image_only"
        and plan["actor"] == "delegated_assistant"
    )
    require(set(plan["helper_sha256s"]) == HELPERS)
    for name, expected in plan["helper_sha256s"].items():
        require(re.fullmatch(r"[0-9a-f]{64}", expected))
        require(sha((ROOT / "scripts" / name).read_bytes()) == expected)
    created, expires = (
        datetime.fromisoformat(plan[k]) for k in ("created_at", "expires_at")
    )
    require(created.tzinfo is not None and expires.tzinfo is not None)
    require(
        0 < (expires - created).total_seconds() <= 7200 and created <= datetime.now(UTC)
    )
    require(recovery or datetime.now(UTC) < expires)
    build, publication, baseline = (
        bound(plan[k]) for k in ("build_plan", "publication", "baseline")
    )
    verify_checkout(build)
    contract = runpy.run_path(str(ROOT / "scripts/staging_release_contract.py"))
    contract["deployment_inputs"](build, plan["build_plan"]["sha256"], publication)
    publication = contract["resolve_publication"](
        build, plan["build_plan"]["sha256"], publication
    )
    require(
        build["release"]["migration_revisions"] == []
        and build["release"]["schema_before"] == build["release"]["schema_after"]
    )
    require(
        baseline["version"] == 1
        and baseline["build_plan_sha256"] == plan["build_plan"]["sha256"]
    )
    require(
        baseline["schema"] == build["release"]["schema_before"]
        and set(baseline["workloads"]) == APPS
    )
    observed = datetime.fromisoformat(baseline["observed_at"])
    require(observed.tzinfo is not None and observed <= datetime.now(UTC))
    require(recovery or (datetime.now(UTC) - observed).total_seconds() <= 7200)
    intent_path = Path(plan["intent_path"])
    require(intent_path.is_absolute() and intent_path.resolve() == intent_path)
    require(intent_path.is_relative_to("/private/tmp") and not intent_path.is_symlink())
    for name, proof in baseline["workloads"].items():
        require(
            proof["name"] == name
            and proof["revision"]
            == proof["ready_revision"]
            == build["release"]["recovery"][name]["revision"]
        )
        require(
            proof["image"]
            == proof["ready_image"]
            == build["release"]["recovery"][name]["image"]
        )
    return build, publication, baseline


def release(
    plan: dict[str, Any],
    plan_hash: str,
    action: str,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recovering = action == "recover"
    build, publication, baseline = prepare(plan, recovery=recovering)
    prior = baseline["workloads"]
    candidate_image = publication["image_reference"]
    if not recovering:
        source_verify(plan["build_plan"], build)
    rows = {
        name: observe(name, recovery=recovering and name == WEB)
        for name in sorted(APPS)
    }
    for name in APPS - {WEB}:
        state_equal(rows[name][0], prior[name])
    if recovering:
        require(
            intent is not None
            and intent["plan_sha256"] == plan_hash
            and intent["operation"] == "web-image-only"
        )
        assert intent is not None
        require(
            intent["mutation_may_start"] is True
            and intent["automatic_retry_allowed"] is False
        )
        current = rows[WEB][0]
        require(current["image"] == candidate_image)
        require(current["ready_image"] in {candidate_image, prior[WEB]["image"]})
        for key in (
            "identity_sha256",
            "configuration_sha256",
            "template_without_image_sha256",
            "effective_runtime_sha256",
        ):
            require(current[key] == prior[WEB][key])
        retained = az(
            WEB,
            ["containerapp", "revision", "show"],
            "--revision",
            prior[WEB]["revision"],
        )["properties"]
        require(retained["provisioningState"] == "Provisioned")
        require(image(retained["template"]) == prior[WEB]["image"])
        require(
            digest(template_projection(retained["template"], without_image=True))
            == prior[WEB]["ready_template_without_image_sha256"]
        )
    else:
        state_equal(rows[WEB][0], prior[WEB])
    require(schema(rows[WEB][1]) == baseline["schema"])
    if action == "check":
        return {"checked": True, "mutated": False, "plan_sha256": plan_hash}
    prepare(plan, recovery=recovering)
    if not recovering:
        source_verify(plan["build_plan"], build)
    # Source reads may take time. Recheck every exact runtime after them,
    # immediately before the durable intent and sole mutation.
    for name in APPS:
        state_equal(
            observe(name, recovery=recovering and name == WEB)[0], rows[name][0]
        )
    prepare(plan, recovery=recovering)
    path = Path(plan["intent_path"] + (".recovery" if recovering else ""))
    write_once(
        path,
        {
            "operation": "web-image-only-recovery" if recovering else "web-image-only",
            "plan_sha256": plan_hash,
            "mutation_may_start": True,
            "started_at": datetime.now(UTC).isoformat(),
            "automatic_retry_allowed": False,
        },
    )
    if recovering:
        target = prior[WEB]["image"]
        az(WEB, ["containerapp", "update"], "--image", target)
    else:
        az(WEB, ["containerapp", "update"], "--image", candidate_image)
        target = candidate_image
    deadline = time.monotonic() + 180
    while True:
        require(time.monotonic() < deadline)
        current = az(WEB, ["containerapp", "show"])["properties"]
        require(image(current["template"]) == target)
        if (
            current["latestRevisionName"] == current["latestReadyRevisionName"]
            and current["provisioningState"] == "Succeeded"
        ):
            break
        time.sleep(3)
    final = {name: observe(name) for name in sorted(APPS)}
    state_equal(final[WEB][0], prior[WEB], new_image=target)
    for name in APPS - {WEB}:
        state_equal(final[name][0], prior[name])
    require(schema(final[WEB][1]) == baseline["schema"])
    for name in APPS:
        state_equal(observe(name)[0], final[name][0])
    result = {
        "complete": True,
        "operation": "web-image-only-recovery" if recovering else "web-image-only",
        "plan_sha256": plan_hash,
        "web": final[WEB][0],
        "worker_scheduler_unchanged": True,
        "schema": baseline["schema"],
        "environment_identity_configuration_preserved": True,
        "publication_performed": False,
        "database_writes": False,
        "clinical_rows_read": False,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    write_once(Path(str(path) + ".result"), result)
    return result


def main(argv: list[str]) -> None:
    require(
        sys.flags.isolated and sys.flags.no_site and os.getuid() == os.geteuid() != 0
    )
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    os.environ["AZURE_LOGGING_ENABLE_LOG_FILE"] = "false"
    os.environ["AZURE_CORE_COLLECT_TELEMETRY"] = "false"
    require(len(argv) in {3, 4, 5})
    action, filename, expected, *extra = argv
    if action == "snapshot":
        require(len(extra) == 1)
        result = snapshot(
            read_bound(filename, expected), {"path": filename, "sha256": expected}
        )
        write_once(Path(extra[0]), result)
        print(
            json.dumps(
                {
                    "snapshot_saved": True,
                    "sha256": sha(canonical(result) + b"\n"),
                    "mutated": False,
                }
            )
        )
        return
    require(action in {"check", "apply", "recover"})
    require(len(extra) == (2 if action == "recover" else 0))
    plan = read_bound(filename, expected)
    if extra:
        require(extra[0] == plan["intent_path"])
    intent = read_bound(*extra) if extra else None
    print(json.dumps(release(plan, expected, action, intent), sort_keys=True))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        raise SystemExit(
            "staging_web_image_only_refused; observe existing intent before any retry"
        ) from None
