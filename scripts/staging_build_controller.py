"""Reviewed local staging build launcher; no publication or Azure operations.

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
import signal
import stat
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, cast

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
CONTROL_SIGNALS = frozenset(
    {signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGALRM}
)
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


class BuildControllerInterrupted(BaseException):
    """Catchable control signal; SIGKILL and host loss remain uncatchable."""


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


def sync_directory(path: Path) -> None:
    """Make an already-atomic result rename durable before reporting success."""
    require(path.is_absolute() and path.resolve() == path)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        require(
            stat.S_ISDIR(before.st_mode)
            and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o700
        )
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        require(
            (before.st_dev, before.st_ino, before.st_uid, before.st_mode)
            == (after.st_dev, after.st_ino, after.st_uid, after.st_mode)
        )
    finally:
        os.close(descriptor)


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
    require(type(plan.get("version")) is int and plan["version"] in {1, 2})
    require(set(plan) == (KEYS | {"release"} if plan["version"] == 2 else KEYS))
    if plan["version"] == 2:
        release = cast(dict[str, Any], plan["release"])
        require(
            type(release) is dict
            and set(release)
            == {
                "schema_before",
                "schema_after",
                "migration_revisions",
                "review_kind",
                "runtime_public",
                "recovery",
            }
        )
        require(release["review_kind"] == "local_cursor")
        for key in ("schema_before", "schema_after"):
            require(
                type(release[key]) is str
                and re.fullmatch(r"[0-9a-f]{12}", release[key])
            )
        revisions = cast(list[str], release["migration_revisions"])
        require(type(revisions) is list and len(revisions) <= 64)
        require(
            all(type(v) is str and re.fullmatch(r"[0-9a-f]{12}", v) for v in revisions)
        )
        require(len(revisions) == len(set(revisions)))
        if revisions:
            require(
                release["schema_before"] != release["schema_after"]
                and revisions[-1] == release["schema_after"]
            )
        else:
            require(release["schema_before"] == release["schema_after"])
    require(plan["action"] == "build_selected_staging_candidate")
    require(plan["actor"] == "delegated_assistant")
    require(
        type(plan["candidate_sha"]) is str
        and re.fullmatch(r"[0-9a-f]{40}", plan["candidate_sha"])
    )
    require(type(plan["candidate_pr"]) is int and 0 < plan["candidate_pr"] < 10**9)
    if plan["version"] == 1:
        require(plan["candidate_sha"] == CANDIDATE and plan["candidate_pr"] == 385)
    for key in ("controller_sha", "controller_reviewed_head"):
        require(type(plan[key]) is str and re.fullmatch(r"[0-9a-f]{40}", plan[key]))
    require(type(plan["build_seconds"]) is int and 0 < plan["build_seconds"] <= 3600)
    for key in ("controller_pr", "controller_review_id"):
        require(type(plan[key]) is int and 0 < plan[key] < 10**15)
    require(type(plan["controller_check_id"]) is int)
    require(
        plan["controller_check_id"] == 0
        if plan["version"] == 2
        else 0 < plan["controller_check_id"] < 10**15
    )
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
    candidate, pr = plan["candidate_sha"], plan["candidate_pr"]
    revisions = (
        tuple(plan["release"]["migration_revisions"])
        if plan["version"] == 2
        else REVISIONS
    )
    before = (
        plan["release"]["schema_before"] if plan["version"] == 2 else "89c31f0ae275"
    )
    after = plan["release"]["schema_after"] if plan["version"] == 2 else REVISIONS[-1]
    selection = record(bound_input(plan, "selection"))
    require(selection["founder_selected"] is True and selection["candidate_pr"] == pr)
    require(selection["candidate_sha"] == candidate)
    delegation = record(bound_input(plan, "delegation"))
    require(delegation["actor"] == "delegated_assistant")
    require(
        delegation["staging_build_approved"] is True
        and delegation["candidate_sha"] == candidate
    )
    require(delegation["production_approved"] is False)
    frontend = bound_input(plan, "frontend", 16384)
    origin = record(bound_input(plan, "frontend_origin"))
    require(origin["complete"] is True and origin["public_fields_only"] is True)
    require(origin["configuration_sha256"] == sha(frontend))
    config = record(frontend)
    require(config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] in {"true", "false"})
    if plan["version"] == 1:
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
    require(outcome["result"] == "PASS" and outcome["source_revision"] == before)
    require(outcome["target_revision"] == after)
    required_checks = (
        "committed_revision_verified",
        "original_rows_preserved",
        "original_constraints_indexes_triggers_preserved",
    )
    if plan["version"] == 1:
        required_checks += (
            "backfill_and_baa_invariants_passed",
            "reader_select_and_four_negative_access_checks_passed",
        )
    else:
        required_checks += ("forward_only", "restore_verified")
    for key in required_checks:
        require(outcome[key] is True)
    require(
        tuple(row["revision"] for row in outcome["actual_online_migrations"])
        == revisions
    )
    return frontend, outcome


def run(
    plan_path: Path,
    expected: str,
    root: Path,
    *,
    check_only: bool,
    source_only: bool = False,
    commit_state: dict[str, Any] | None = None,
    emit_success: Callable[[dict[str, Any]], object] | None = None,
) -> dict[str, Any]:
    raw_plan = read_bound(plan_path, expected, 65536)
    plan = record(raw_plan)
    validate_plan(plan)
    candidate, pr = plan["candidate_sha"], plan["candidate_pr"]
    schema_after = (
        plan["release"]["schema_after"] if plan["version"] == 2 else REVISIONS[-1]
    )
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
        candidate,
        str(pr),
        schema_after,
        plan["controller_sha"],
        "CosmaNeura/cosmaneura",
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
        review_kind="local_cursor" if plan["version"] == 2 else "bugbot",
    )
    observed = source.collect_build_source(root, request)
    source_archive = observed.source_archive
    if source_only:
        return {
            "local_bindings_passed": True,
            "authenticated_source_checked": True,
            "build_started": False,
            "plan_sha256": expected,
            "candidate_sha": candidate,
            "approval_pr_number": pr,
            "schema_head": schema_after,
            "controller_sha": plan["controller_sha"],
            "github_user_sha256": observed.github_user_sha256,
            "source_archive_sha256": observed.source_archive_sha256,
            "protection_sha256": observed.protection_sha256,
            "github_actions_enabled": observed.github_actions_enabled,
        }
    build_inputs = inputs.BuildInputs(
        candidate,
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
    require(commit_state is None or (type(commit_state) is dict and not commit_state))
    require(emit_success is None or callable(emit_success))
    parent = Path(plan["attempt_parent"])
    require(parent.resolve() == parent and not parent.exists())
    result: dict[str, Any] = {
        "complete": False,
        "actor": "delegated_assistant",
        "plan_sha256": expected,
        "module_sha256s": plan["module_sha256s"],
        "candidate_sha": candidate,
        "controller_sha": plan["controller_sha"],
        "source_archive_sha256": sha(source_archive),
        "github_actions_enabled": observed.github_actions_enabled,
        "protection_sha256": observed.protection_sha256,
        "frontend_configuration_sha256": sha(frontend),
        "started_at": datetime.now(UTC).isoformat(),
        "publication_authorized": False,
        "deployment_authorized": False,
    }
    phase = "create_attempt"
    attempt_created = False
    success: dict[str, Any] | None = None
    success_raw: bytes | None = None
    success_renamed = False
    committed = False
    try:
        with process.defer_control_signals():
            parent.mkdir(mode=0o700)
            attempt_created = True

        phase = "prepare_owned_inputs"

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
        phase = "prepare_success_record"
        success_raw = canonical(success)
        success_path = parent / "controller-result.success.tmp"
        inputs.write_exclusive(success_path, success_raw, 0o400)
        require(read_bound(success_path, sha(success_raw), 65536) == success_raw)

        phase = "commit_success_record"
        with process.defer_control_signals():
            require(not (parent / "controller-result.json").exists())
            os.rename(success_path, parent / "controller-result.json")
            success_renamed = True
            sync_directory(parent)
            committed = True
            if commit_state is not None:
                commit_state["result"] = success
            if emit_success is not None:
                try:
                    emit_success(success)
                except BaseException:
                    # The durable record is authoritative. Output failure after
                    # commit cannot turn the exact invocation into a failure.
                    pass
        return success
    except BaseException as error:
        if committed:
            require(success is not None)
            return cast(dict[str, Any], success)
        result.update(
            complete=False,
            phase=phase,
            error_class=type(error).__name__,
            finished_at=datetime.now(UTC).isoformat(),
        )
        if attempt_created:
            final_path = parent / "controller-result.json"
            failure_raw = canonical(result)
            with process.defer_control_signals():
                if success_renamed:
                    require(success_raw is not None)
                    require(
                        read_bound(final_path, sha(cast(bytes, success_raw)), 65536)
                        == success_raw
                    )
                    failure_path = parent / "controller-result.failure.tmp"
                    inputs.write_exclusive(failure_path, failure_raw, 0o400)
                    os.replace(failure_path, final_path)
                else:
                    require(not final_path.exists())
                    inputs.write_exclusive(final_path, failure_raw, 0o400)
                sync_directory(parent)
        raise


def emit_result(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True), flush=True)


def interrupt_build(signum: int, _frame: object) -> None:
    raise BuildControllerInterrupted(signum)


def block_control_signals_through_exit() -> None:
    """A committed CLI result exits zero; SIGKILL/host loss are uncatchable."""
    signal.pthread_sigmask(signal.SIG_BLOCK, CONTROL_SIGNALS)


def main() -> int:
    require(sys.flags.isolated and sys.flags.no_site and not sys.flags.optimize)
    require(os.getuid() == os.geteuid() != 0)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    require(
        len(sys.argv) == 4
        and sys.argv[1] in {"--check-only", "--verify-source", "--run"}
    )
    run_mode = sys.argv[1] == "--run"
    commit_state: dict[str, Any] = {}
    try:
        if run_mode:
            for signum in CONTROL_SIGNALS:
                signal.signal(signum, interrupt_build)
        result = run(
            Path(sys.argv[2]),
            sys.argv[3],
            Path(__file__).resolve().parents[1],
            check_only=sys.argv[1] == "--check-only",
            source_only=sys.argv[1] == "--verify-source",
            commit_state=commit_state if run_mode else None,
            emit_success=emit_result if run_mode else None,
        )
        if run_mode:
            require(commit_state.get("result") == result and result["complete"] is True)
            block_control_signals_through_exit()
            return 0
        emit_result(result)
        return 0
    except BaseException as error:
        if commit_state.get("result", {}).get("complete") is True:
            block_control_signals_through_exit()
            return 0
        try:
            emit_result(
                {
                    "complete": False,
                    "error_class": type(error).__name__,
                    "code": "staging_build_controller_refused",
                }
            )
        except BaseException:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
