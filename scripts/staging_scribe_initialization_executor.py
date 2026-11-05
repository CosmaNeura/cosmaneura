"""Source-bound Scribe initialization bridge; private state never leaves memory.

Only the preserving deployer invokes this bridge. A separate post-publication
plan binds the known image digest; build and publication receipts stay immutable.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import runpy
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PURE = runpy.run_path(str(ROOT / "scripts/staging_scribe_public_initialization.py"))
RUNTIME = runpy.run_path(str(ROOT / "scripts/staging_runtime_operations.py"))
CONTRACT = RUNTIME["CONTRACT"]
require = PURE["require"]
OPERATION = "scribe-initialize"


def observe(*, ready: bool = True) -> dict:
    """Bound management reads only; resolved private values stay in this process."""
    result = {}
    for name in PURE["PHASES"]:
        raw = RUNTIME["az"](OPERATION, name, ["containerapp", "show"])
        value = raw["properties"]
        revision = value["latestRevisionName"]
        if ready:
            require(revision == value["latestReadyRevisionName"])
            health = RUNTIME["revision"](OPERATION, name, revision)
            require(
                health["healthState"] == "Healthy"
                and health["provisioningState"] == "Provisioned"
            )
        secrets = RUNTIME["az"](
            OPERATION, name, ["containerapp", "secret", "list"], "--show-values"
        )
        pairs = [(item["name"], item["value"]) for item in secrets]
        require(len(pairs) == len({key for key, _ in pairs}))
        result[name] = {
            "name": name,
            "revision": revision,
            "template": value["template"],
            "configuration": value["configuration"],
            "identity": raw["identity"],
            "secret_values": dict(pairs),
        }
        PURE["environment"](result[name])
    return result


def source_check(build_path: str, build_hash: str, build: dict) -> None:
    raw = RUNTIME["command"](
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "scripts/staging_build_controller.py"),
            "--verify-source",
            build_path,
            build_hash,
        ]
    )
    checked = json.loads(raw, object_pairs_hook=PURE["unique"])
    require(
        checked["local_bindings_passed"] is True
        and checked["authenticated_source_checked"] is True
        and checked["build_started"] is False
        and checked["candidate_sha"] == build["candidate_sha"]
        and checked["controller_sha"] == build["controller_sha"]
        and checked["approval_pr_number"] == build["candidate_pr"]
        and checked["schema_head"] == build["release"]["schema_after"]
        and checked["plan_sha256"] == build_hash
        and checked["github_user_sha256"] == build["github_user_sha256"]
        and checked["source_archive_sha256"] == build["source_archive_sha256"]
    )


def context(
    plan: dict, build: dict, publication: dict, identity: str, schema: str
) -> dict:
    release = build["release"]
    expected = {
        "candidate_sha": build["candidate_sha"],
        "controller_sha": build["controller_sha"],
        "approval_pr": build["candidate_pr"],
        "founder": "JohnClem",
        "identity_sha256": identity,
        "schema_before": release["schema_before"],
        "schema_after": release["schema_after"],
        "candidate_image": publication["image_reference"],
    }
    require(plan["bindings"] == expected)
    require(plan["recovery"]["image"] == publication["image_reference"])
    require(
        all(release["runtime_public"].get(k) == v for k, v in plan["controls"].items())
    )
    return expected | {"schema_revision": schema, "plan_sha256": PURE["digest"](plan)}


def update_arguments(name: str, row: dict, controls: dict) -> list[str]:
    selected = PURE["FIELDS"] if name == PURE["API"] else {PURE["LEDGER"]}
    require(
        name in PURE["PHASES"]
        and set(controls) == selected
        and controls[PURE["LEDGER"]] == "true"
    )
    return [
        "--image",
        row["image"],
        "--revision-suffix",
        row["revision"].split("--", 1)[1],
        "--set-env-vars",
        *[key + "=" + controls[key] for key in sorted(controls)],
    ]


def resolved_consistency(configuration: dict, observed: dict, build: dict) -> None:
    """Migration inputs and retained refs must resolve to the same private bytes."""
    require(type(configuration) is dict)
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    arrays = {}
    for key in (
        "PUBLIC_VARS",
        "SCRIBE_API_ONLY_PUBLIC_VARS",
        "API_WORKER_PUBLIC_VARS",
        "WORKER_ONLY_PUBLIC_VARS",
    ):
        match = re.search(r"^" + key + r"=\((.*?)\)", source, re.MULTILINE | re.DOTALL)
        if match is None:
            raise ValueError("staging_scribe_public_scope_missing")
        arrays[key] = set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", match.group(1)))
    for name, state in observed.items():
        env = PURE["environment"](state)
        for key, entry in env.items():
            if "secretRef" in entry:
                require(
                    configuration.get(key) == state["secret_values"][entry["secretRef"]]
                )
        scope = set(arrays["PUBLIC_VARS"])
        if name == PURE["API"]:
            scope |= arrays["SCRIBE_API_ONLY_PUBLIC_VARS"]
        if name in {PURE["API"], "cosmaneura-staging-worker"}:
            scope |= arrays["API_WORKER_PUBLIC_VARS"]
        if name == "cosmaneura-staging-worker":
            scope |= arrays["WORKER_ONLY_PUBLIC_VARS"]
        for key, value in build["release"]["runtime_public"].items():
            if key not in PURE["FIELDS"] and key in scope:
                require(key in env and env[key] == {"name": key, "value": value})


def verify_file(fd: int) -> None:
    value = os.fstat(fd)
    require(
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink == 1
        and not value.st_mode & 0o077
    )


class Journal:
    """A stable lock protects atomically replaced hash-only phase records."""

    def __init__(self, path: Path, plan: dict, raw_hash: str):
        require(
            path.is_absolute()
            and path.resolve() == path
            and path.is_relative_to("/private/tmp")
        )
        parent = path.parent.stat()
        require(parent.st_uid == os.geteuid() and not parent.st_mode & 0o022)
        self.path, self.plan = path, plan
        self.binding = {
            "schema": "scribe_initialization_journal_v1",
            "plan_sha256": PURE["digest"](plan),
            "raw_plan_sha256": raw_hash,
        }
        self.fd = os.open(
            str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        verify_file(self.fd)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def close(self) -> None:
        os.close(self.fd)

    def plan_row(self, name: str, closed: bool) -> dict:
        return (
            self.plan["recovery"]["states"][name]
            if closed
            else self.plan["desired"][name]
        )

    def read(self) -> dict | None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        try:
            verify_file(fd)
            raw = os.read(fd, 65537)
            require(len(raw) <= 65536)
            value = json.loads(raw, object_pairs_hook=PURE["unique"])
        finally:
            os.close(fd)
        require(
            set(value)
            == set(self.binding) | {"entry", "completed", "intent", "closed_completed"}
            and all(value[k] == v for k, v in self.binding.items())
        )
        require(
            value["entry"]
            == {
                "enabled": True,
                "plan_sha256": self.binding["plan_sha256"],
                "before_sha256": self.plan["before_sha256"],
                "desired_sha256": self.plan["desired_sha256"],
                "phase_order": list(PURE["PHASES"]),
            }
        )
        for key, closed in (("completed", False), ("closed_completed", True)):
            receipts = value[key]
            require(type(receipts) is list and len(receipts) <= len(PURE["PHASES"]))
            for index, receipt in enumerate(receipts):
                name = PURE["PHASES"][index]
                expected = {"name": name, **self.plan_row(name, closed)}
                if not closed:
                    expected["plan_sha256"] = self.binding["plan_sha256"]
                require(receipt == expected)
        intent = value["intent"]
        if intent is not None:
            require(set(intent) == {"name", "closed", "target"})
            require(type(intent["closed"]) is bool and intent["name"] in PURE["PHASES"])
            require(intent["target"] == self.plan_row(intent["name"], intent["closed"]))
            receipts = value["closed_completed" if intent["closed"] else "completed"]
            require(len(receipts) < len(PURE["PHASES"]))
            require(intent["name"] == PURE["PHASES"][len(receipts)])
        return value

    def write(self, value: dict) -> None:
        temporary = self.path.with_name(self.path.name + ".next")
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write((json.dumps(value, sort_keys=True) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()


def step(
    plan: dict,
    ctx: dict,
    journal: Journal,
    configuration: dict,
    build: dict,
    *,
    recover: bool = False,
) -> dict:
    value = journal.read()
    if value is None:
        raise ValueError("staging_scribe_journal_missing")
    observed = observe(ready=not recover)
    resolved_consistency(configuration, observed, build)
    now = datetime.now(UTC)
    if recover:
        PURE["check_plan"](plan, ctx, now)
        require(ctx["schema_revision"] == plan["bindings"]["schema_after"])
        done = value["closed_completed"]
        for index, name in enumerate(PURE["PHASES"]):
            row = PURE["state_row"](observed[name])
            if index < len(done) or (
                value["intent"] is not None
                and value["intent"]["closed"]
                and name == value["intent"]["name"]
            ):
                require(row == plan["recovery"]["states"][name])
            elif index < len(value["completed"]):
                require(row == plan["desired"][name])
            elif (
                value["intent"] is not None
                and not value["intent"]["closed"]
                and name == value["intent"]["name"]
            ):
                require(row in [plan["before"][name], plan["desired"][name]])
            else:
                require(row == plan["before"][name])
        name = PURE["PHASES"][len(done)] if len(done) < len(PURE["PHASES"]) else None
        if name is None:
            require(value["intent"] is None)
            return PURE["validate_closed_recovery"](plan, observed, ctx, now)
        row = plan["recovery"]["states"][name]
        if value["intent"] is not None and value["intent"]["closed"]:
            # A recorded exact healthy CLOSED target may be acknowledged,
            # never resubmitted or adopted merely because it looks plausible.
            RUNTIME["ready"](OPERATION, name, row["revision"], row["image"])
            require(observe(ready=False) == observed)
            done.append({"name": name, **row})
            value["intent"] = None
            journal.write(value)
            return {
                "workload": name,
                "closed": True,
                "acknowledged": True,
                "target_sha256": row["sha256"],
            }
        selected = PURE["FIELDS"] if name == PURE["API"] else {PURE["LEDGER"]}
        controls = {key: plan["recovery"]["controls"][key] for key in selected}
    else:
        require(value["intent"] is None and not value["closed_completed"])
        result = PURE["validate_phase"](plan, observed, ctx, value["completed"], now)
        name = result["next_workload"]
        if name is None:
            return result
        row, controls = result["expected_target"], result["public_updates"]
    result = {}
    value["intent"] = {"name": name, "closed": recover, "target": row}
    journal.write(value)
    RUNTIME["az"](
        OPERATION,
        name,
        ["containerapp", "update"],
        *update_arguments(name, row, controls),
    )
    RUNTIME["ready"](OPERATION, name, row["revision"], row["image"])
    actual = observe(ready=not recover)
    require(PURE["state_row"](actual[name]) == row)
    require(all(actual[n] == observed[n] for n in PURE["PHASES"] if n != name))
    if recover:
        value["closed_completed"].append({"name": name, **row})
    else:
        value["completed"].append(
            {"name": name, "plan_sha256": PURE["digest"](plan), **row}
        )
        result = PURE["validate_phase"](plan, actual, ctx, value["completed"], now)
    value["intent"] = None
    journal.write(value)
    return (
        {"workload": name, "closed": True, "target_sha256": row["sha256"]}
        if recover
        else result
    )


def main(argv: list[str]) -> None:
    require(len(argv) == 11 and argv[0] in {"before", "step", "closed-step", "finish"})
    (
        action,
        path,
        raw_hash,
        build_path,
        build_hash,
        pub_path,
        pub_hash,
        identity,
        schema,
        journal_path,
        desired_hash,
    ) = argv
    build = CONTRACT["bound_json"](build_path, build_hash)
    publication = CONTRACT["bound_json"](pub_path, pub_hash)
    CONTRACT["deployment_inputs"](build, build_hash, publication)
    publication = CONTRACT["resolve_publication"](build, build_hash, publication)
    plan = CONTRACT["bound_json"](path, raw_hash)
    require(plan["desired_sha256"] == desired_hash)
    ctx = context(plan, build, publication, identity, schema)
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024)
    payload = json.loads(raw, object_pairs_hook=PURE["unique"])
    require(set(payload) == {"configuration"})
    configuration = payload["configuration"]
    source_check(build_path, build_hash, build)
    journal = Journal(Path(journal_path), plan, raw_hash)
    try:
        if action == "before":
            require(journal.read() is None)
            observed = observe()
            resolved_consistency(configuration, observed, build)
            entry = PURE["validate_transition"](plan, observed, ctx, datetime.now(UTC))
            journal.write(
                journal.binding
                | {
                    "entry": entry,
                    "completed": [],
                    "intent": None,
                    "closed_completed": [],
                }
            )
            result = entry
        elif action == "finish":
            value = journal.read()
            if value is None:
                raise ValueError("staging_scribe_journal_missing")
            require(value["intent"] is None and not value["closed_completed"])
            result = PURE["validate_phase"](
                plan, observe(), ctx, value["completed"], datetime.now(UTC)
            )
            require(result["complete"])
        else:
            result = step(
                plan,
                ctx,
                journal,
                configuration,
                build,
                recover=action == "closed-step",
            )
        print(json.dumps(result, sort_keys=True))
    finally:
        journal.close()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        raise SystemExit("staging_scribe_initialization_executor_refused") from None
