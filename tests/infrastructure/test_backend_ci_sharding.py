from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PYTHON_JUST = ROOT / "just" / "python.just"
WORKFLOWS = (
    ROOT / ".github/workflows/pull_request_checks.yml",
    ROOT / ".github/workflows/build_and_publish_manual.yml",
    ROOT / ".github/workflows/deploy_staging_candidate.yml",
)
SHARDS = ("application", "infrastructure", "integration")


def _workflow(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _run_commands(job: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        step["run"]
        for step in job["steps"]
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_ci_recipes_are_explicit_disjoint_and_exhaustive() -> None:
    source = PYTHON_JUST.read_text(encoding="utf-8")
    py_setup_start = source.index("py_setup:")
    py_setup_end = source.index("\n# clean entire py project", py_setup_start)
    py_setup_recipe = source[py_setup_start:py_setup_end]
    assert "playwright install chromium --only-shell" not in py_setup_recipe
    assert "uv run playwright install chromium" in py_setup_recipe

    expected_commands = {
        "application": (
            "uv run pytest tests --verbosity=2 \\\n"
            "\t\t--ignore=tests/infrastructure \\\n"
            "\t\t--ignore=tests/integration \\\n"
        ),
        "infrastructure": "uv run pytest tests/infrastructure --verbosity=2",
        "integration": "uv run pytest tests/integration --verbosity=2",
    }
    for shard, command in expected_commands.items():
        assert source.count(f"py_test-ci-{shard}:") == 1
        assert f"py_test-ci-{shard} is CI-only" in source
        assert command in source

    application_start = source.index("py_test-ci-application:")
    infrastructure_start = source.index("py_test-ci-infrastructure:")
    application_recipe = source[application_start:infrastructure_start]
    integration_start = source.index("py_test-ci-integration:")
    integration_end = source.index("\n# run tests with", integration_start)
    integration_recipe = source[integration_start:integration_end]
    shard_recipes = source[application_start:integration_end]
    for recipe in (application_recipe, integration_recipe):
        assert "just py_js-build" in recipe
    assert "playwright install chromium --only-shell" not in application_recipe
    assert "localias" not in application_recipe.lower()
    assert "playwright install chromium --only-shell" not in integration_recipe
    assert "playwright install chromium --only-shell" not in shard_recipes

    test_files = {
        path.relative_to(ROOT)
        for pattern in ("test_*.py", "*_test.py")
        for path in (ROOT / "tests").rglob(pattern)
    }
    selected = {
        "infrastructure": {
            path for path in test_files if path.parts[1] == "infrastructure"
        },
        "integration": {path for path in test_files if path.parts[1] == "integration"},
    }
    selected["application"] = (
        test_files - selected["infrastructure"] - selected["integration"]
    )

    assert all(selected.values())
    assert set().union(*selected.values()) == test_files
    for left_index, left in enumerate(SHARDS):
        for right in SHARDS[left_index + 1 :]:
            assert selected[left].isdisjoint(selected[right])


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_backend_workers_are_parallel_isolated_and_fail_closed(path: Path) -> None:
    jobs = _workflow(path)["jobs"]
    quality = jobs["backend-quality"]
    tests = jobs["backend-tests"]
    aggregator = jobs["app"]

    assert quality["name"] in {"Backend quality", "Candidate backend quality"}
    assert tests["strategy"]["fail-fast"] is False
    matrix = tests["strategy"]["matrix"]["include"]
    assert [entry["shard"] for entry in matrix] == list(SHARDS)
    assert [entry["apt-packages"] for entry in matrix] == [
        "zsh",
        "zsh",
        "zsh libnss3-tools",
    ]
    assert "needs" not in quality or quality["needs"] == "authorize"
    assert "needs" not in tests or tests["needs"] == "authorize"

    quality_commands = _run_commands(quality)
    test_commands = _run_commands(tests)
    for commands in (quality_commands, test_commands):
        assert "just up --fast" in commands
        assert "just py_setup" in commands
        assert "just db_migrate" in commands
    assert "just py_lint" in quality_commands
    assert "just db_lint" in quality_commands
    assert "just py_lint" not in test_commands
    assert "just db_lint" not in test_commands
    assert "just py_test-ci-${{ matrix.shard }}" in test_commands

    localias_install = _named_step(tests, "Install SHA-verified Localias")
    localias_start_name = (
        "Start exact candidate SHA-verified local test routing"
        if path.name == "deploy_staging_candidate.yml"
        else "Start SHA-verified Localias"
    )
    localias_start = _named_step(tests, localias_start_name)
    chromium_install = _named_step(tests, "Install integration Chromium headless shell")
    for step in (localias_install, chromium_install, localias_start):
        assert step["if"] == "${{ matrix.shard == 'integration' }}"
    assert chromium_install["run"] == "uv run playwright install chromium --only-shell"
    steps = tests["steps"]
    assert (
        next(
            index
            for index, step in enumerate(steps)
            if step.get("run") == "just py_setup"
        )
        < steps.index(chromium_install)
        < steps.index(localias_start)
    )
    assert str(tests).count("playwright install chromium --only-shell") == 1
    assert not any("localias" in command.lower() for command in quality_commands)
    assert not any("playwright install" in command for command in quality_commands)

    artifact = _named_step(
        tests,
        (
            "Upload candidate backend shard test artifacts"
            if path.name == "deploy_staging_candidate.yml"
            else "Upload shard test artifacts"
        ),
    )
    assert artifact["if"] == "${{ failure() }}"
    assert "${{ matrix.shard }}" in artifact["with"]["name"]
    assert "${{ github.run_id }}" in artifact["with"]["name"]
    assert "${{ github.run_attempt }}" in artifact["with"]["name"]

    assert aggregator["name"] == (
        "Candidate Backend"
        if path.name == "deploy_staging_candidate.yml"
        else "Backend"
    )
    assert aggregator["needs"] == ["backend-quality", "backend-tests"]
    assert aggregator["if"] == "${{ always() }}"
    assert "continue-on-error" not in quality
    assert "continue-on-error" not in tests
    assert "continue-on-error" not in aggregator


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
@pytest.mark.parametrize(
    ("quality_result", "tests_result", "expected_status"),
    [
        ("success", "success", 0),
        ("failure", "success", 1),
        ("cancelled", "success", 1),
        ("skipped", "success", 1),
        ("success", "failure", 1),
        ("success", "cancelled", 1),
        ("success", "skipped", 1),
    ],
)
def test_backend_aggregator_rejects_every_non_success_dependency(
    path: Path,
    quality_result: str,
    tests_result: str,
    expected_status: int,
) -> None:
    aggregator = _workflow(path)["jobs"]["app"]
    command = _named_step(aggregator, "Require every backend prerequisite to succeed")[
        "run"
    ]
    environment = os.environ | {
        "BACKEND_QUALITY_RESULT": quality_result,
        "BACKEND_TESTS_RESULT": tests_result,
    }
    completed = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{command}"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    assert completed.returncode == expected_status


def test_candidate_backend_workers_reverify_sha_without_shared_caches() -> None:
    jobs = _workflow(ROOT / ".github/workflows/deploy_staging_candidate.yml")["jobs"]
    for job_name in ("backend-quality", "backend-tests"):
        job = jobs[job_name]
        checkout = _named_step(job, "Check out exact candidate")
        verification = _named_step(job, "Verify exact candidate checkout")
        toolchain = _named_step(
            job, "Install exact candidate toolchain without shared caches"
        )
        assert checkout["with"]["ref"] == (
            "${{ needs.authorize.outputs.verified_revision }}"
        )
        assert checkout["with"]["persist-credentials"] is False
        assert 'test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"' in verification["run"]
        assert toolchain["with"]["cache"] == "false"


def test_shards_do_not_weaken_or_recombine_coverage() -> None:
    recipe_source = PYTHON_JUST.read_text(encoding="utf-8")
    shard_source = recipe_source[
        recipe_source.index("py_test-ci-application:") : recipe_source.index(
            "\n# run tests with"
        )
    ]
    assert "--cov-append" not in shard_source
    assert "xdist" not in shard_source

    for path in WORKFLOWS:
        tests = _workflow(path)["jobs"]["backend-tests"]
        assert "continue-on-error" not in tests
        assert "download-artifact" not in str(tests)
