"""Reuse one immutable acceptance run after an exact test-only repair."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

REPOSITORY = "CosmaNeura/cosmaneura"
VALIDATION_RUN = 40591309946
VALIDATED_SHA = "955527ff7c3f91a9fb0fc5529a4de82aeff696e2"
TEST_PATH = "tests/authority_kernel/scribe_capture_history_grounding_test.py"
TEST_BASE_HASH = "a291649c4b4b703aedc9ab01fa05d96350b4d6d4b59602c38d69c70afe2958f3"
TEST_REPAIR_HASH = "ab3c75b713ec275e4e78c3cc6b7b3c10ff3889c1262ff367feb7bbc5279a4671"
FAILED_NODE = (
    TEST_PATH
    + "::test_strict_saved_shape_and_existing_plan_resolver_are_preserved[True]"
)
NEW_CONTROL_FILES = {
    ".github/workflows/production_release_verified.yml",
    "scripts/verify_one_time_release_reuse.py",
    "tests/one_time_release_reuse_test.py",
    "docs/flight-plans/2026-10-27-verified-production-release.md",
}
SHARDS = {
    "application",
    "integration",
    "infrastructure-general",
    "infrastructure-controller",
    "infrastructure-receipt-io",
    "infrastructure-sanitizer-foundation",
    "infrastructure-sanitizer-source-state-authority",
    "infrastructure-sanitizer-source-state-context",
    "infrastructure-sanitizer-source-state-integrity",
    "infrastructure-sanitizer-evidence",
    "infrastructure-sanitizer-evidence-integrity",
    "infrastructure-sanitizer-executor",
    "infrastructure-sanitizer-observer",
    "infrastructure-sanitizer-observer-runtime",
    "infrastructure-sanitizer-observer-output",
    "infrastructure-sanitizer-observer-connection-plan",
    "infrastructure-sanitizer-observer-connection-plan-state",
    "infrastructure-sanitizer-observer-connection-plan-output",
    "infrastructure-sanitizer-observer-connection-plan-contract",
    "infrastructure-sanitizer-bootstrap-binding",
    "infrastructure-sanitizer-bootstrap-binding-digest",
    "infrastructure-sanitizer-bootstrap-binding-ancestry",
    "infrastructure-sanitizer-bootstrap-binding-state",
    "infrastructure-sanitizer-bootstrap-binding-output",
    "infrastructure-sanitizer-bootstrap-binding-duplicate",
    "infrastructure-sanitizer-bootstrap-binding-tamper",
    "infrastructure-sanitizer-bootstrap-binding-failure",
    "infrastructure-sanitizer-observer-integrity",
    "infrastructure-sanitizer-data-contract-executor",
    "infrastructure-sanitizer-source-snapshot",
    "infrastructure-restore",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_validation(run: dict, job_response: dict, log: str) -> None:
    expected = {
        "id": VALIDATION_RUN,
        "name": "Build & Publish (manual)",
        "path": ".github/workflows/build_and_publish_local.yml",
        "head_sha": VALIDATED_SHA,
        "head_branch": "master",
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "failure",
    }
    require(
        all(run.get(k) == v for k, v in expected.items()),
        "Validation run provenance/completion differs.",
    )
    require(
        run.get("repository", {}).get("full_name") == REPOSITORY,
        "Wrong validation repository.",
    )
    require(
        all(
            run.get(k, {}).get("login") == "JohnClem"
            for k in ("actor", "triggering_actor")
        ),
        "Wrong validation actor.",
    )
    conclusions = {f"Backend tests ({shard})": "success" for shard in SHARDS}
    conclusions.update(
        {
            "Backend tests (application)": "failure",
            "Backend quality": "success",
            "Frontend": "success",
            "Authorize production promotion": "success",
            "Release scope": "success",
            "Backend": "failure",
            "🚀 Deploy": "skipped",
        }
    )
    jobs = job_response.get("jobs", [])
    require(
        job_response.get("total_count") == len(conclusions) == len(jobs),
        "Missing or additional validation jobs.",
    )
    require(
        len({j.get("name") for j in jobs}) == len(jobs), "Duplicate validation jobs."
    )
    require(
        {j.get("name") for j in jobs} == set(conclusions),
        "Validation job names differ.",
    )
    for job in jobs:
        require(
            job.get("run_id") == VALIDATION_RUN
            and job.get("head_sha") == VALIDATED_SHA
            and job.get("run_attempt") == 1,
            "Job provenance differs.",
        )
        require(
            job.get("status") == "completed" and bool(job.get("completed_at")),
            "Validation job incomplete.",
        )
        require(
            job.get("conclusion") == conclusions[job["name"]],
            "Unexpected validation failure.",
        )
        if job["name"] == "🚀 Deploy":
            require(not job.get("steps"), "Original deployment executed.")
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", log)
    plain = re.sub(r"(?m)^\d{4}-\d\d-\d\dT\S+Z ", "", plain)
    failures = re.findall(r"(?m)^FAILED (.+)$", plain)
    require(
        len(failures) == 1
        and failures[0].startswith(FAILED_NODE + " - AssertionError:"),
        "Failed node differs.",
    )
    summaries = re.findall(
        r"(?m)^=+ (\d+) failed, (\d+) passed, (\d+) skipped, (\d+) warnings in .+ =+$",
        plain,
    )
    require(
        summaries == [("1", "4586", "1639", "21")],
        "Acceptance totals differ or logs are incomplete.",
    )
    require(
        not re.search(
            r"(?m)^(ERROR (?:tests/|collecting\b|\S+::)|SUBFAIL |.* errors? during collection)",
            plain,
        ),
        "Additional collection/subtest error.",
    )
    require(
        "+ - Unidentified speaker: We will follow up in seven days." in plain
        and "- Transcript — Unidentified speaker:" in plain,
        "Expected exact stale assertion was not observed.",
    )


def output(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def verify_source(target: str, reviewed: str) -> None:
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", target))
        and bool(re.fullmatch(r"[0-9a-f]{40}", reviewed)),
        "Invalid source SHA.",
    )
    require(
        output(["git", "rev-parse", "HEAD"]) == target,
        "Checkout differs from release SHA.",
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", VALIDATED_SHA, target], check=True
    )
    subprocess.run(["git", "merge-base", "--is-ancestor", reviewed, target], check=True)
    subprocess.run(["git", "diff", "--quiet", reviewed, target], check=True)
    changes = output(
        ["git", "diff", "--name-status", "--no-renames", VALIDATED_SHA, target]
    ).splitlines()
    require(
        set(changes) == {"M\t" + TEST_PATH} | {"A\t" + p for p in NEW_CONTROL_FILES},
        "Runtime/build/configuration source changed.",
    )
    old = subprocess.check_output(["git", "show", VALIDATED_SHA + ":" + TEST_PATH])
    require(
        hashlib.sha256(old).hexdigest() == TEST_BASE_HASH,
        "Original assertion file differs.",
    )
    require(
        hashlib.sha256(Path(TEST_PATH).read_bytes()).hexdigest() == TEST_REPAIR_HASH,
        "Assertion repair differs.",
    )


def main() -> None:
    require(
        os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong release repository."
    )
    require(
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_REF") == "refs/heads/master",
        "Release must be manual master.",
    )
    require(
        os.environ.get("GITHUB_ACTOR") == "JohnClem"
        and os.environ.get("GITHUB_TRIGGERING_ACTOR") == "JohnClem",
        "Release requires founder actors.",
    )
    require(
        os.environ.get("LOCAL_ASSERTION_REPAIR_VERIFIED") == "true",
        "Local failed-case verification is required.",
    )
    target = os.environ["GITHUB_SHA"]
    verify_source(target, os.environ["REVIEWED_COMMIT"])
    require(
        output(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/git/ref/heads/master",
                "--jq",
                ".object.sha",
            ]
        )
        == target,
        "Master advanced.",
    )
    run = json.loads(
        output(["gh", "api", f"repos/{REPOSITORY}/actions/runs/{VALIDATION_RUN}"])
    )
    jobs = json.loads(
        output(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/actions/runs/{VALIDATION_RUN}/jobs?per_page=100&filter=latest",
            ]
        )
    )
    application = [
        j
        for j in jobs.get("jobs", [])
        if j.get("name") == "Backend tests (application)"
    ]
    require(len(application) == 1, "Application job missing or duplicated.")
    log = output(
        [
            "gh",
            "api",
            "--allow-escape-sequences",
            f"repos/{REPOSITORY}/actions/jobs/{application[0]['id']}/logs",
        ]
    )
    verify_validation(run, jobs, log)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write(
            f"Reused immutable validation run {VALIDATION_RUN} at {VALIDATED_SHA}. "
            "Every other job succeeded; 4586 application tests passed and 1639 were skipped. "
            "The sole stale equality assertion has an exact hash-bound, locally verified repair. "
            "Runtime, migration, configuration, action, tool and build source bytes are unchanged.\n"
        )
    print("Exact validation and unchanged production source verified; no tests rerun.")


if __name__ == "__main__":
    main()
