"""Reviewed Actions-off staging build launcher; no publication or Azure operations.

Invoke with isolated Python and an independently reviewed plan's exact SHA256.
The actual founder selection/delegation and approved invocation supply authority;
local JSON is corroborating evidence, never a substitute for that authorization.
All helper bytes are verified before import. Authentication uses the existing
bounded GitHub reader, separately from the credential-free build environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import stat
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

MODULE_ORDER = (
    "verify_staging_candidate.py",
    "staging_human_build_inputs.py",
    "staging_human_build_process.py",
    "staging_snapshot_source_provenance.py",
    "staging_build_source.py",
    "staging_human_build_executor.py",
)
SELF = "staging_build_controller.py"
MODULES = frozenset((*MODULE_ORDER, SELF))
CANDIDATE = "866bd4bc01e0e7be32c59eccdaf9dac3f495c7de"
REVISIONS = ("d0a4c8e2f611", "e7a9c3d5f102", "da6e199668a4", "9a2399212488")
KEYS = {
    "version",
    "action",
    "actor",
    "candidate_sha",
    "candidate_pr",
    "controller_sha",
    "created_at",
    "expires_at",
    "build_seconds",
    "module_sha256s",
    "github_user_sha256",
    "source_archive_sha256",
    "controller_pr",
    "controller_reviewed_head",
    "controller_review_id",
    "controller_check_id",
    "controller_review_sha256",
    "selection",
    "delegation",
    "frontend",
    "frontend_origin",
    "rehearsal",
    "native_archive",
    "engine",
    "attempt_parent",
}


class BuildControllerRefused(RuntimeError):
    """Content-free refusal; preserve partial artifacts and never retry silently."""


def require(condition: object) -> None:
    if not condition:
        raise BuildControllerRefused("staging_build_controller_refused")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def record(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict)
    return value


def digest(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def read_bound(path: Path, expected: str, maximum: int) -> bytes:
    require(digest(expected) and path.is_absolute() and path.resolve() == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        require(before.st_uid == os.geteuid() and not before.st_mode & 0o022)
        require(0 < before.st_size <= maximum)
        chunks, total = [], 0
        while chunk := os.read(fd, min(65536, maximum - total + 1)):
            total += len(chunk)
            require(total <= maximum)
            chunks.append(chunk)
        raw = b"".join(chunks)
        fields = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        identity = tuple(getattr(before, key) for key in fields)
        for info in (os.fstat(fd), path.lstat()):
            require(tuple(getattr(info, key) for key in fields) == identity)
        require(len(raw) == before.st_size and sha(raw) == expected)
        return raw
    finally:
        os.close(fd)


def bound_input(plan: dict[str, Any], name: str, maximum: int = 65536) -> bytes:
    spec = plan[name]
    require(type(spec) is dict and set(spec) == {"path", "sha256"})
    require(type(spec["path"]) is str)
    return read_bound(Path(cast(str, spec["path"])), cast(str, spec["sha256"]), maximum)


def clock_check(plan: dict[str, Any], *, starting: bool = False) -> None:
    created = datetime.fromisoformat(plan["created_at"])
    expires = datetime.fromisoformat(plan["expires_at"])
    require(created.utcoffset() is not None and expires.utcoffset() is not None)
    now = datetime.now(UTC)
    require(
        created <= now < expires and 0 < (expires - created).total_seconds() <= 7200
    )
    if starting:
        require((expires - now).total_seconds() >= plan["build_seconds"] + 600)


def validate_plan(plan: dict[str, Any]) -> None:
    require(set(plan) == KEYS and type(plan["version"]) is int and plan["version"] == 1)
    require(plan["action"] == "build_selected_staging_candidate")
    require(plan["actor"] == "delegated_assistant")
    require(plan["candidate_sha"] == CANDIDATE and type(plan["candidate_pr"]) is int)
    require(plan["candidate_pr"] == 385)
    for key in ("controller_sha", "controller_reviewed_head"):
        require(type(plan[key]) is str and re.fullmatch(r"[0-9a-f]{40}", plan[key]))
    require(type(plan["build_seconds"]) is int and 0 < plan["build_seconds"] <= 3600)
    for key in ("controller_pr", "controller_review_id", "controller_check_id"):
        require(type(plan[key]) is int and 0 < plan[key] < 10**15)
    for key in (
        "github_user_sha256",
        "source_archive_sha256",
        "controller_review_sha256",
    ):
        require(digest(plan[key]))
    hashes = plan["module_sha256s"]
    require(type(hashes) is dict and set(hashes) == MODULES)
    require(all(digest(value) for value in hashes.values()))
    engine = cast(dict[str, Any], plan["engine"])
    require(
        type(engine) is dict
        and set(engine) == {"id_sha256", "socket_device", "socket_inode"}
    )
    require(digest(engine["id_sha256"]))
    require(
        all(
            type(engine[k]) is int and cast(int, engine[k]) > 0
            for k in ("socket_device", "socket_inode")
        )
    )
    require(type(plan["attempt_parent"]) is str)
    parent = Path(plan["attempt_parent"])
    require(parent.parent == Path("/private/tmp"))
    require(re.fullmatch(r"cosmaneura-staging-build-[0-9a-f]{32}", parent.name))
    clock_check(plan)


def module_bytes(root: Path, plan: dict[str, Any]) -> dict[str, bytes]:
    return {
        name: read_bound(root / "scripts" / name, plan["module_sha256s"][name], 1024**2)
        for name in sorted(MODULES)
    }


def load_modules(raw: dict[str, bytes], root: Path) -> dict[str, types.ModuleType]:
    require(set(raw) == MODULES)
    require(all(name.removesuffix(".py") not in sys.modules for name in MODULE_ORDER))
    loaded = {}
    for filename in MODULE_ORDER:
        name = filename.removesuffix(".py")
        module = types.ModuleType(name)
        module.__file__ = str(root / "scripts" / filename)
        sys.modules[name] = module
        exec(compile(raw[filename], module.__file__, "exec"), module.__dict__)
        loaded[name] = module
    return loaded


def input_evidence(plan: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    selection = record(bound_input(plan, "selection"))
    require(selection["founder_selected"] is True and selection["candidate_pr"] == 385)
    require(selection["candidate_sha"] == CANDIDATE)
    delegation = record(bound_input(plan, "delegation"))
    require(delegation["actor"] == "delegated_assistant")
    require(
        delegation["staging_build_approved"] is True
        and delegation["candidate_sha"] == CANDIDATE
    )
    require(delegation["production_approved"] is False)
    frontend = bound_input(plan, "frontend", 16384)
    origin = record(bound_input(plan, "frontend_origin"))
    require(origin["complete"] is True and origin["public_fields_only"] is True)
    require(origin["configuration_sha256"] == sha(frontend))
    config = record(frontend)
    require(config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] == "false")
    names = {
        "VITE_SENTRY_DSN",
        "VITE_CLERK_PUBLIC_KEY",
        "VITE_POSTHOG_KEY",
        "VITE_POSTHOG_HOST",
    }
    require(type(origin["fields"]) is list and len(origin["fields"]) == len(names))
    fields = cast(list[dict[str, Any]], origin["fields"])
    require({item["name"] for item in fields} == names)
    for item in fields:
        require(sha(config[item["name"]].encode()) == item["sha256"])
    rehearsal = record(bound_input(plan, "rehearsal"))
    require(rehearsal["complete"] is True and rehearsal["archive_unchanged"] is True)
    require(
        rehearsal["owned_containers_stopped"] is True
        and rehearsal["actor"] == "delegated_assistant"
    )
    require(
        all(
            digest(rehearsal[key])
            for key in ("runner_sha256", "worker_sha256", "plan_sha256")
        )
    )
    outcome = rehearsal["rehearsal"]
    require(
        outcome["result"] == "PASS" and outcome["source_revision"] == "89c31f0ae275"
    )
    require(outcome["target_revision"] == REVISIONS[-1])
    for key in (
        "committed_revision_verified",
        "original_rows_preserved",
        "original_constraints_indexes_triggers_preserved",
        "backfill_and_baa_invariants_passed",
        "reader_select_and_four_negative_access_checks_passed",
    ):
        require(outcome[key] is True)
    require(
        tuple(row["revision"] for row in outcome["actual_online_migrations"])
        == REVISIONS
    )
    return frontend, outcome


def run(
    plan_path: Path,
    expected: str,
    root: Path,
    *,
    check_only: bool,
    source_only: bool = False,
) -> dict[str, Any]:
    raw_plan = read_bound(plan_path, expected, 65536)
    plan = record(raw_plan)
    validate_plan(plan)
    raw_modules = module_bytes(root, plan)
    frontend, rehearsal = input_evidence(plan)
    native = bound_input(plan, "native_archive", 4 * 1024**2)
    if check_only:
        return {
            "local_bindings_passed": True,
            "authenticated_source_checked": False,
            "build_started": False,
            "plan_sha256": expected,
        }
    clock_check(plan, starting=not source_only)
    modules = load_modules(raw_modules, root)
    source = modules["staging_build_source"]
    inputs = modules["staging_human_build_inputs"]
    process = modules["staging_human_build_process"]
    executor = modules["staging_human_build_executor"]
    inputs.public_configuration(frontend, sha(frontend))
    inputs.native_builder_bytes(native)
    context = modules["verify_staging_candidate"].HumanCandidateContext(
        CANDIDATE, "385", REVISIONS[-1], plan["controller_sha"], "CosmaNeura/cosmaneura"
    )
    request = source.SourceRequest(
        context,
        plan["github_user_sha256"],
        plan["source_archive_sha256"],
        plan["controller_pr"],
        plan["controller_reviewed_head"],
        plan["controller_review_id"],
        plan["controller_check_id"],
        plan["controller_review_sha256"],
        tuple(sorted(plan["module_sha256s"].items())),
    )
    observed = source.collect_build_source(root, request)
    source_archive = observed.source_archive
    if source_only:
        return {
            "local_bindings_passed": True,
            "authenticated_source_checked": True,
            "build_started": False,
            "plan_sha256": expected,
            "candidate_sha": CANDIDATE,
            "approval_pr_number": 385,
            "schema_head": REVISIONS[-1],
            "controller_sha": plan["controller_sha"],
            "github_user_sha256": observed.github_user_sha256,
            "source_archive_sha256": observed.source_archive_sha256,
            "protection_sha256": observed.protection_sha256,
        }
    build_inputs = inputs.BuildInputs(
        CANDIDATE,
        sha(source_archive),
        sha(frontend),
        datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    entries = inputs.source_entries(source_archive, build_inputs)
    migration_hashes = {
        sha(body)
        for name, body, _mode in entries
        if name.startswith("migrations/versions/")
    }
    require(
        all(
            row["source_sha256"] in migration_hashes
            for row in rehearsal["actual_online_migrations"]
        )
    )
    parent = Path(plan["attempt_parent"])
    require(parent.resolve() == parent and not parent.exists())
    parent.mkdir(mode=0o700)
    result: dict[str, Any] = {
        "complete": False,
        "actor": "delegated_assistant",
        "plan_sha256": expected,
        "module_sha256s": plan["module_sha256s"],
        "candidate_sha": CANDIDATE,
        "controller_sha": plan["controller_sha"],
        "source_archive_sha256": sha(source_archive),
        "frontend_configuration_sha256": sha(frontend),
        "started_at": datetime.now(UTC).isoformat(),
        "publication_authorized": False,
        "deployment_authorized": False,
    }
    phase = "prepare_owned_inputs"
    try:

        def authority() -> None:
            clock_check(plan)
            require(read_bound(plan_path, expected, 65536) == raw_plan)
            require(module_bytes(root, plan) == raw_modules)
            require(input_evidence(plan) == (frontend, rehearsal))
            reader = source.BuildSourceReader(root, request)
            require(reader.protections() == observed.protection_sha256)
            reader.final_boundaries()
            clock_check(plan)

        authority()
        prepared = inputs.prepare_build_inputs(
            parent,
            build_inputs,
            source_archive=source_archive,
            frontend_json=frontend,
            native_archive=native,
        )
        result["build_root"] = str(prepared.root)
        phase = "build"

        finalized_archive: list[tuple[Any, str, int]] = []

        def finalize_archive(archive: Any) -> None:
            nonlocal phase
            require(phase == "build" and result["complete"] is False)
            phase = "verify_successful_call"
            authority()
            require(not (prepared.root / "artifacts/build-failure.json").exists())
            require(
                executor.file_sha(archive.path, executor.MAX_IMAGE)
                == (archive.sha256, archive.size)
            )
            build_metadata_sha256, build_metadata_bytes = executor.file_sha(
                archive.metadata_path, 65536
            )
            clock_check(plan)
            finalized_archive.append(
                (archive, build_metadata_sha256, build_metadata_bytes)
            )

        archive = executor.build_candidate(
            prepared,
            source_archive,
            engine=executor.EngineBinding(**plan["engine"]),
            validate_authority=authority,
            finalize_archive=finalize_archive,
            seconds=plan["build_seconds"],
        )
        require(len(finalized_archive) == 1 and archive is finalized_archive[0][0])
        _, build_metadata_sha256, build_metadata_bytes = finalized_archive[0]
        clock_check(plan)
        success = result | dict(
            complete=True,
            image_id=archive.image_id,
            image_archive=str(archive.path),
            image_archive_sha256=archive.sha256,
            image_archive_bytes=archive.size,
            build_metadata=str(archive.metadata_path),
            build_metadata_sha256=build_metadata_sha256,
            build_metadata_bytes=build_metadata_bytes,
            public_assets_sha256=archive.public_assets_sha256,
            finished_at=datetime.now(UTC).isoformat(),
        )
        success_raw = canonical(success)
        success_path = parent / "controller-result.success.tmp"
        inputs.write_exclusive(success_path, success_raw, 0o400)
        require(read_bound(success_path, sha(success_raw), 65536) == success_raw)
    except BaseException as error:
        result.update(
            complete=False,
            phase=phase,
            error_class=type(error).__name__,
            finished_at=datetime.now(UTC).isoformat(),
        )
        inputs.write_exclusive(
            parent / "controller-result.json", canonical(result), 0o400
        )
        raise

    phase = "commit_success_record"
    committed = False
    try:
        with process.defer_control_signals():
            require(not (parent / "controller-result.json").exists())
            os.rename(success_path, parent / "controller-result.json")
            committed = True
            return success
    except BaseException as error:
        if committed:
            return success
        result.update(
            complete=False,
            phase=phase,
            error_class=type(error).__name__,
            finished_at=datetime.now(UTC).isoformat(),
        )
        inputs.write_exclusive(
            parent / "controller-result.json", canonical(result), 0o400
        )
        raise


def main() -> int:
    require(sys.flags.isolated and sys.flags.no_site and not sys.flags.optimize)
    require(os.getuid() == os.geteuid() != 0)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    require(
        len(sys.argv) == 4
        and sys.argv[1] in {"--check-only", "--verify-source", "--run"}
    )
    try:
        result = run(
            Path(sys.argv[2]),
            sys.argv[3],
            Path(__file__).resolve().parents[1],
            check_only=sys.argv[1] == "--check-only",
            source_only=sys.argv[1] == "--verify-source",
        )
    except BaseException as error:
        print(
            json.dumps(
                {
                    "complete": False,
                    "error_class": type(error).__name__,
                    "code": "staging_build_controller_refused",
                }
            ),
            flush=True,
        )
        return 2
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
