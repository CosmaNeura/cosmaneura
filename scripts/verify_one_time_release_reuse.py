"""Reuse unchanged acceptance plus one separately reviewed clinical delta."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

REPOSITORY = "CosmaNeura/cosmaneura"
VALIDATION_RUN = 40591309946
VALIDATED_SHA = "955527ff7c3f91a9fb0fc5529a4de82aeff696e2"
SOURCE_BASE_SHA = "4ff9318298e332f67b18117ccbabfd3764f569d7"
CLINICAL_HEAD = "08a5cdacc572530b1ad2cd5eb8908696a1f1b8d3"
CLINICAL_REVIEW_ID = 6454808424
CLINICAL_REVIEW_HASH = (
    "19bb1d71cab36fea3d893a01db4d6b8179f4163c30b24ea7fac072ff2c6bddf5"
)
TEST_PATH = "tests/authority_kernel/scribe_capture_history_grounding_test.py"
TEST_BASE_HASH = "a291649c4b4b703aedc9ab01fa05d96350b4d6d4b59602c38d69c70afe2958f3"
TEST_REPAIR_HASH = "14cea999882da5a9b864857cc65cabf50597cf779229d715eb5b91fdd29547b0"
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
CLINICAL_DELTA_HASHES = {
    "app/services/scribe_capture_history_grounding.py": "ef1ae31c27df7f96f598dd57fb9a8313628623bda68d998817e8334e8b9103b7",
    "app/services/scribe_capture_notes.py": "c3c24f811cd17d5c7ee7547d18775e61d5c70e57bce72fb89f2cc03d183c04c3",
    "app/services/scribe_capture_plan_grounding.py": "a9e8f81231e4f59d9ec79c0cec942d652a967858f58a4b21505ee7a445e6661e",
    "docs/flight-plans/2026-10-27-capture-note-quality.md": "75dbd087ecf39da0a2517bf52d9c42233c95aa14e6f15b4d5e83b16aa0c88c62",
    "tests/authority_kernel/scribe_capture_compact_attribution_test.py": "673f4f4673998d9b37ca7ea910367778574b7902c8a537ce497839c9d4dbce94",
    "tests/authority_kernel/scribe_capture_history_grounding_test.py": "14cea999882da5a9b864857cc65cabf50597cf779229d715eb5b91fdd29547b0",
    "tests/authority_kernel/scribe_capture_medication_verification_test.py": "81ad2f7e4f6fbb0d7b0bb39fbb74ca5d8ed24cb0516d9aac24736f95cfc204df",
    "tests/authority_kernel/scribe_capture_question_context_persistence_test.py": "04d8c1b447237ef22092f1e14e64a06a129628e6baffa6f73f64834e37f1c931",
    "tests/authority_kernel/scribe_capture_retention_test.py": "2cffa249db052d0665c3ead520d37d29d1716ffae0bbddd6e377904607867ba0",
    "tests/authority_kernel/scribe_capture_soap_medication_test.py": "1cf92ac74bbddc141ad0ebf73dc86e98469e28f40319d1293ebb50352d633a5c",
    "tests/capture_question_context_test.py": "0e632d00313d14f989129b52e8aba54b1a74a9715a6483763b2c52f1ed25e26a",
    "tests/capture_source_instruction_test.py": "e97483d6c9b8db55cdbd583b52931a5eacb512fa43b33a43a318c299f24eea32",
    "tools/capture-retention/note-quality-model-receipts.json": "13ad64153f25cf1e4079006f61b3e8a2fd11ab7ec7f606875a7f9307814d7bcf",
    "tools/capture-retention/note-quality-validation.json": "a7016e6f200e10a43844b21edfff999db1a47e16de3dbd779cf738c932f1af9d",
    "tools/capture-retention/r2-canonical38-synthetic.json": "57e93ab4cf25b8cbc429c792ed61347a8121a8db6aded9663ef94d736181a240",
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


def verify_clinical_review(pr: dict, review: dict) -> None:
    require(
        pr.get("number") == 854
        and pr.get("merged") is True
        and pr.get("merge_commit_sha") == SOURCE_BASE_SHA
        and pr.get("head", {}).get("sha") == CLINICAL_HEAD
        and pr.get("base", {}).get("ref") == "master"
        and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY,
        "Clinical change provenance differs.",
    )
    require(
        review.get("id") == CLINICAL_REVIEW_ID
        and review.get("issue_url")
        == f"https://api.github.com/repos/{REPOSITORY}/issues/854"
        and review.get("user", {}).get("login") == "JohnClem"
        and hashlib.sha256(review.get("body", "").encode()).hexdigest()
        == CLINICAL_REVIEW_HASH,
        "Exact accepted clinical review differs.",
    )


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
        ["git", "merge-base", "--is-ancestor", VALIDATED_SHA, SOURCE_BASE_SHA],
        check=True,
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", CLINICAL_HEAD, SOURCE_BASE_SHA],
        check=True,
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", SOURCE_BASE_SHA, target], check=True
    )
    subprocess.run(["git", "merge-base", "--is-ancestor", reviewed, target], check=True)
    subprocess.run(["git", "diff", "--quiet", reviewed, target], check=True)
    changes = output(
        ["git", "diff", "--name-status", "--no-renames", SOURCE_BASE_SHA, target]
    ).splitlines()
    require(
        set(changes) == {"A\t" + p for p in NEW_CONTROL_FILES},
        "Runtime/build/configuration source changed.",
    )
    clinical_changes = output(
        ["git", "diff", "--name-only", VALIDATED_SHA, SOURCE_BASE_SHA]
    ).splitlines()
    require(
        set(clinical_changes) == set(CLINICAL_DELTA_HASHES),
        "Clinical delta paths differ.",
    )
    for path, expected_hash in CLINICAL_DELTA_HASHES.items():
        value = subprocess.check_output(["git", "show", SOURCE_BASE_SHA + ":" + path])
        require(
            hashlib.sha256(value).hexdigest() == expected_hash,
            "Clinical delta bytes differ.",
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
    clinical_pr = json.loads(output(["gh", "api", f"repos/{REPOSITORY}/pulls/854"]))
    clinical_review = json.loads(
        output(
            ["gh", "api", f"repos/{REPOSITORY}/issues/comments/{CLINICAL_REVIEW_ID}"]
        )
    )
    verify_clinical_review(clinical_pr, clinical_review)
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
            "The sole stale equality assertion is repaired in the separately accepted clinical change. "
            f"Clinical delta {SOURCE_BASE_SHA} is bound to exact head {CLINICAL_HEAD} and review {CLINICAL_REVIEW_ID}: "
            "217 focused author tests, 18 independent production tests and exact 17-response replay. "
            "Every other production source byte retains the original acceptance; no test was rerun.\n"
        )
    print(
        "Retained acceptance and exact reviewed clinical delta verified; no tests rerun."
    )


if __name__ == "__main__":
    main()
