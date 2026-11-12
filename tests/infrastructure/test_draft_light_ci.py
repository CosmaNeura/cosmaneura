from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/pull_request_checks.yml"
MASTER_JOB_IDS = ("backend-quality", "backend-tests", "app", "web")
MASTER_JOB_NAMES = {
    "backend-quality": "Backend quality",
    "backend-tests": "Backend tests (${{ matrix.shard }})",
    "app": "Backend",
    "web": "Frontend",
}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


# Proof (b): real follow-up change so a newer synchronize cancels the prior
# in-progress Pull Request Checks run via the workflow concurrency group.
def test_draft_light_ci_mode_and_fail_closed_backend_gate() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert "ci-mode" in jobs
    assert jobs["ci-mode"]["runs-on"] == "ubuntu-24.04"
    assert jobs["ci-mode"]["outputs"]["full"] == "${{ steps.decide.outputs.full }}"

    backend_tests = jobs["backend-tests"]
    assert backend_tests["needs"] == ["ci-mode"]
    assert backend_tests["if"] == "needs.ci-mode.outputs.full != 'false'"

    assert "if" not in jobs["web"]
    assert "if" not in jobs["backend-quality"]
    assert "ci-mode.outputs.full" not in str(jobs["web"])
    assert "ci-mode.outputs.full" not in str(jobs["backend-quality"])
    assert jobs["app"]["if"] == "${{ always() }}"
    assert "ci-mode.outputs.full" not in str(jobs["app"]["if"])

    backend = jobs["app"]
    assert backend["name"] == "Backend"
    assert backend["needs"] == ["backend-quality", "backend-tests", "ci-mode"]
    gate = next(
        step
        for step in backend["steps"]
        if step.get("name") == "Require every backend prerequisite to succeed"
    )
    assert gate["run"] == (
        'if [[ "$BACKEND_QUALITY_RESULT" != "success" '
        '|| "$BACKEND_TESTS_RESULT" != "success" ]]; then\n'
        '  if [[ "$BACKEND_TESTS_RESULT" == "skipped" ]]; then\n'
        '    echo "::error::Light draft run is red by design '
        '(backend-tests skipped). Add the ci:full label for a full run."\n'
        "  else\n"
        '    echo "::error::Backend prerequisites did not all succeed '
        '(quality=$BACKEND_QUALITY_RESULT, tests=$BACKEND_TESTS_RESULT)."\n'
        "  fi\n"
        "  exit 1\n"
        "fi\n"
    )
    assert "continue-on-error" not in backend
    assert "exit 0" not in gate["run"]

    preamble = source[: source.index("jobs:\n")]
    assert "paths:" not in preamble
    assert "paths" not in workflow.get("on", {}).get("pull_request", {})

    concurrency = workflow["concurrency"]
    assert (
        concurrency["group"]
        == "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
    )
    assert (
        concurrency["cancel-in-progress"]
        == "${{ github.event_name == 'pull_request' }}"
    )

    for job_id in MASTER_JOB_IDS:
        assert job_id in jobs
        assert jobs[job_id]["name"] == MASTER_JOB_NAMES[job_id]
    assert set(jobs) == set(MASTER_JOB_IDS) | {"ci-mode"}
