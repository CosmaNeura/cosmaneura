import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import verify_one_time_release_reuse as reuse


def evidence():
    run = {
        "id": reuse.VALIDATION_RUN,
        "name": "Build & Publish (manual)",
        "path": ".github/workflows/build_and_publish_local.yml",
        "head_sha": reuse.VALIDATED_SHA,
        "head_branch": "master",
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "failure",
        "repository": {"full_name": reuse.REPOSITORY},
        "actor": {"login": "JohnClem"},
        "triggering_actor": {"login": "JohnClem"},
    }
    states = {f"Backend tests ({s})": "success" for s in reuse.SHARDS}
    states.update(
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
    jobs = {
        "total_count": len(states),
        "jobs": [
            {
                "name": name,
                "conclusion": state,
                "run_id": reuse.VALIDATION_RUN,
                "head_sha": reuse.VALIDATED_SHA,
                "run_attempt": 1,
                "status": "completed",
                "completed_at": "2026-10-27T22:00:00Z",
                "steps": [],
            }
            for name, state in states.items()
        ],
    }
    log = (
        f"FAILED {reuse.FAILED_NODE} - AssertionError: stale expectation\n"
        "+ - Unidentified speaker: We will follow up in seven days.\n"
        "- Transcript — Unidentified speaker:\n"
        "================ 1 failed, 4586 passed, 1639 skipped, 21 warnings in 303.43s (0:05:03) ================\n"
    )
    return run, jobs, log


def test_exact_completed_acceptance_is_reusable():
    reuse.verify_validation(*evidence())


@pytest.mark.parametrize(
    "line,accepted",
    [
        ("ERROR [celery.redirected] task framework event", True),
        ("ERROR [kombu.connection] task framework event", True),
        ("ERROR tests/other.py - ImportError: broken collection", False),
        ("ERROR collecting tests/other.py", False),
        ("ERROR other.py::test_setup - RuntimeError: broken fixture", False),
        ("SUBFAIL tests/other.py::test_subcase", False),
    ],
)
def test_real_logger_lines_are_distinct_from_pytest_errors(line, accepted):
    run, jobs, log = evidence()
    if accepted:
        reuse.verify_validation(run, jobs, line + "\n" + log)
    else:
        with pytest.raises(ValueError):
            reuse.verify_validation(run, jobs, line + "\n" + log)


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", 1),
        ("head_sha", "0" * 40),
        ("event", "pull_request"),
        ("head_branch", "other"),
        ("run_attempt", 2),
        ("status", "in_progress"),
        ("conclusion", "cancelled"),
        ("path", ".github/workflows/other.yml"),
    ],
)
def test_wrong_or_incomplete_run_cannot_supply_acceptance(key, value):
    run, jobs, log = evidence()
    run[key] = value
    with pytest.raises(ValueError):
        reuse.verify_validation(run, jobs, log)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "extra", "failed", "rerun", "incomplete", "deployed"],
)
def test_job_inventory_and_successes_cannot_be_relaxed(mutation):
    run, jobs, log = evidence()
    if mutation == "missing":
        jobs["jobs"].pop()
    elif mutation == "duplicate":
        jobs["jobs"][-1] = copy.deepcopy(jobs["jobs"][0])
    elif mutation == "extra":
        jobs["jobs"].append(copy.deepcopy(jobs["jobs"][0]))
        jobs["total_count"] += 1
    elif mutation == "failed":
        jobs["jobs"][0]["conclusion"] = "timed_out"
    elif mutation == "rerun":
        jobs["jobs"][0]["run_attempt"] = 2
    elif mutation == "incomplete":
        jobs["jobs"][0]["status"] = "in_progress"
    else:
        jobs["jobs"][-1]["steps"] = [{"name": "Deploy", "status": "completed"}]
    with pytest.raises(ValueError):
        reuse.verify_validation(run, jobs, log)


@pytest.mark.parametrize(
    "mutation",
    ["node", "additional_failure", "totals", "partial", "collection", "subtest"],
)
def test_only_the_exact_single_stale_assertion_can_be_repaired(mutation):
    run, jobs, log = evidence()
    if mutation == "node":
        log = log.replace("[True]", "[False]")
    elif mutation == "additional_failure":
        log += "FAILED tests/other.py::test_bad - AssertionError: unsafe\n"
    elif mutation == "totals":
        log = log.replace("4586 passed", "4585 passed")
    elif mutation == "partial":
        log = log.split("================")[0]
    elif mutation == "collection":
        log += "ERROR tests/other.py\n"
    else:
        log += "SUBFAIL tests/other.py\n"
    with pytest.raises(ValueError):
        reuse.verify_validation(run, jobs, log)


def test_existing_build_publish_and_deployment_steps_and_locks_are_preserved():
    original = yaml.safe_load(
        Path(".github/workflows/build_and_publish_local.yml").read_text()
    )
    release = yaml.safe_load(
        Path(".github/workflows/production_release_verified.yml").read_text()
    )
    assert release["concurrency"] == original["concurrency"]
    before = original["jobs"]["deploy"]
    after = release["jobs"]["deploy"]
    assert after["concurrency"] == before["concurrency"]
    assert after["environment"] == before["environment"]
    assert after["permissions"] == before["permissions"] | {
        "pull-requests": "read",
        "issues": "read",
    }
    assert after["env"] == before["env"]
    steps = [
        s
        for s in after["steps"]
        if s.get("name")
        != "Verify exact prior acceptance and unchanged production source"
    ]
    assert steps == before["steps"]
    guard_index = next(
        i
        for i, s in enumerate(after["steps"])
        if s.get("name")
        == "Verify exact prior acceptance and unchanged production source"
    )
    credentials_index = next(
        i
        for i, s in enumerate(after["steps"])
        if s.get("name") == "Resolve production frontend build configuration"
    )
    assert guard_index < credentials_index


CLINICAL_REVIEW_BODY = "Reviewed exact head 08a5cdacc572530b1ad2cd5eb8908696a1f1b8d3. No blocking source findings. The medication verification limit is restricted to newly generated drafts and existing eligible fields; saved-note early returns and authorization/source checks remain intact. The compact renderer preserves complete selected text, speaker qualification and order.\n\nAll 15 changed files and 17 retained model/request/source binding chains were checked. Independent production tests passed 18/18; the exact 17-response replay reproduced the archived result byte-for-byte. The author’s separate focused suite reports 217 passes. The documented mmHg expansion, mixed editorial source text, duplicate follow-up and raw person-fixture parser limitation remain; this review does not claim live or universal clinical qualification.\n"


@pytest.mark.parametrize(
    "mutation",
    ["valid", "head", "merge", "unmerged", "repository", "review_id", "author", "body"],
)
def test_separately_accepted_clinical_source_is_exactly_bound(mutation):
    pr: dict[str, Any] = {
        "number": 854,
        "merged": True,
        "merge_commit_sha": reuse.SOURCE_BASE_SHA,
        "head": {"sha": reuse.CLINICAL_HEAD},
        "base": {"ref": "master", "repo": {"full_name": reuse.REPOSITORY}},
    }
    review: dict[str, Any] = {
        "id": reuse.CLINICAL_REVIEW_ID,
        "issue_url": f"https://api.github.com/repos/{reuse.REPOSITORY}/issues/854",
        "user": {"login": "JohnClem"},
        "body": CLINICAL_REVIEW_BODY,
    }
    if mutation == "head":
        pr["head"]["sha"] = "0" * 40
    elif mutation == "merge":
        pr["merge_commit_sha"] = "0" * 40
    elif mutation == "unmerged":
        pr["merged"] = False
    elif mutation == "repository":
        pr["base"]["repo"]["full_name"] = "other/repository"
    elif mutation == "review_id":
        review["id"] = 1
    elif mutation == "author":
        review["user"]["login"] = "other"
    elif mutation == "body":
        review["body"] += " Changed acceptance."
    if mutation == "valid":
        reuse.verify_clinical_review(pr, review)
    else:
        with pytest.raises(ValueError):
            reuse.verify_clinical_review(pr, review)
