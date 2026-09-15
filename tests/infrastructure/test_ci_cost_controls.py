"""Cost controls fail closed and do not silently remove merge-time coverage."""

import ast
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.ci_backend_shards import selected_files, shard_for

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name="pull_request_checks.yml"):
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def run_gate(job, environment):
    command = workflow()["jobs"][job]["steps"][0]["run"]
    return subprocess.run(
        ["bash", "-euc", command],
        env={"PATH": os.environ["PATH"], **environment},
        capture_output=True,
        check=False,
    ).returncode


def test_every_job_has_a_finite_timeout():
    for path in (*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")):
        for name, job in workflow(path.name)["jobs"].items():
            assert 1 <= int(job["timeout-minutes"]) <= 60, (path, name)


def test_pr_matrix_and_full_fallback_remain_bounded_and_exhaustive():
    document = workflow()
    filters = yaml.safe_load((ROOT / ".github/ci-paths.yml").read_text())
    assert {
        "app/**",
        "migrations/**",
        "tests/**",
        "scripts/**",
        "contracts/**",
        "infra*/**",
        ".github/**",
        "just/**",
        "justfile",
        "*.toml",
        "uv.lock",
        "requirements*",
        "web/**",
        ".env*",
        ".tool-versions",
        ".service-versions",
    } <= set(filters["backend"])
    assert {"web/**", "just/**", ".github/**", "*lock*", "*config*"} <= set(
        filters["frontend"]
    )
    assert "docs/**" not in filters["backend"]
    assert document["on"] == {
        "pull_request": {},
        "push": {"branches": ["master"]},
        "workflow_dispatch": {},
    }
    assert document["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "true",
    }
    jobs = document["jobs"]
    strategy = jobs["backend-tests"]["strategy"]
    assert strategy["fail-fast"] == "false"
    assert strategy["matrix"]["include"] == [
        {"shard": "application", "apt-packages": "zsh"},
        {"shard": "infrastructure-1", "apt-packages": "zsh"},
        {"shard": "infrastructure-2", "apt-packages": "zsh"},
        {"shard": "integration", "apt-packages": "zsh libnss3-tools"},
    ]
    for job, output in (
        ("backend-quality", "backend"),
        ("backend-tests", "backend"),
        ("frontend-tests", "frontend"),
    ):
        assert jobs[job]["needs"] == "changes"
        assert (
            jobs[job]["if"] == "${{ needs.changes.outputs." + output + " == 'true' }}"
        )
    for job, name, dependencies in (
        ("app", "Backend", ["changes", "backend-quality", "backend-tests"]),
        ("web", "Frontend", ["changes", "frontend-tests"]),
    ):
        assert jobs[job]["name"] == name
        assert jobs[job]["needs"] == dependencies
        assert jobs[job]["if"] == "${{ always() }}"


@pytest.mark.parametrize(
    "event", ["pull_request", "push", "workflow_dispatch", "unexpected"]
)
@pytest.mark.parametrize("value", ["true", "false", "", "invalid"])
def test_scope_is_validated_and_master_manual_always_select_all(event, value, tmp_path):
    output = tmp_path / "output"
    step = workflow()["jobs"]["changes"]["steps"][-1]
    result = subprocess.run(
        ["bash", "-euc", step["run"]],
        env={
            "PATH": os.environ["PATH"],
            "EVENT": event,
            "BACKEND": value,
            "FRONTEND": value,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        check=False,
    )
    if event in ("push", "workflow_dispatch"):
        assert result.returncode == 0
        assert output.read_text() == "backend=true\nfrontend=true\n"
    elif event == "pull_request" and value in ("true", "false"):
        assert result.returncode == 0
    else:
        assert result.returncode != 0


@pytest.mark.parametrize("changes", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("scope", ["true", "false", "", "invalid"])
@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_required_gates_accept_only_success_or_explicit_skip(changes, scope, result):
    expected = changes == "success" and (
        (scope == "true" and result == "success")
        or (scope == "false" and result == "skipped")
    )
    for job, environment in (
        (
            "app",
            {
                "BACKEND_CHANGED": scope,
                "BACKEND_QUALITY_RESULT": result,
                "BACKEND_TESTS_RESULT": result,
            },
        ),
        ("web", {"FRONTEND_CHANGED": scope, "FRONTEND_RESULT": result}),
    ):
        assert (
            run_gate(job, {"CHANGES_RESULT": changes, **environment}) == 0
        ) == expected


def test_pr_has_no_secrets_or_write_token_and_all_actions_are_pinned():
    document = workflow()
    assert document["permissions"] == {"contents": "read"}
    source = (WORKFLOWS / "pull_request_checks.yml").read_text()
    assert "secrets." not in source
    for job in document["jobs"].values():
        assert "permissions" not in job
        assert "continue-on-error" not in job
        for step in job["steps"]:
            if "uses" not in step or step["uses"].startswith("./"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[a-f0-9]{40}", step["uses"])
            if step["uses"].startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] == "false"
    detector = document["jobs"]["changes"]["steps"][1]
    assert (
        detector["uses"]
        == "dorny/paths-filter@0e4a8c6effa4802afeda77dc8d303f8176d7dfad"
    )
    assert detector["with"]["token"] == ""
    assert document["jobs"]["changes"]["steps"][0]["with"]["fetch-depth"] == "0"


def test_manual_deployment_does_not_run_on_push_or_indirect_triggers():
    assert set(workflow("build_and_publish_manual.yml")["on"]) == {"workflow_dispatch"}


def test_partitions_cover_every_infrastructure_test_file_once():
    one, two = set(selected_files(1)), set(selected_files(2))
    expected = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "tests/infrastructure").rglob("*.py")
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    }
    assert one and two and not one.intersection(two)
    assert one | two == expected
    for path in expected:
        assert (path in one) == (shard_for(path) == 1)
    with pytest.raises(ValueError):
        selected_files(3)


def test_only_offline_workers_skip_services_and_parallelize():
    steps = workflow()["jobs"]["backend-tests"]["steps"]
    for command in ("just up --fast", "just db_migrate"):
        step = next(step for step in steps if step.get("run") == command)
        assert step["if"] == "${{ !startsWith(matrix.shard, 'infrastructure-') }}"
    source = (ROOT / "just/python.just").read_text()
    for shard in (1, 2):
        recipe = source.split(f"py_test-ci-infrastructure-{shard}:\n")[1].split("\n\n")[
            0
        ]
        assert f"scripts/ci_backend_shards.py {shard}" in recipe
        assert "--confcutdir=tests/infrastructure" in recipe and "-n auto" in recipe
        assert "{{PYTEST_COV_PARAMS}}" in recipe
    for name in ("application", "integration"):
        recipe = source.split(f"py_test-ci-{name}:\n")[1].split("\n\n")[0]
        assert "-n " not in recipe and "--confcutdir" not in recipe


def test_cache_opt_out_is_preserved_and_cache_keys_include_locks():
    action = yaml.load(
        (ROOT / ".github/actions/common-setup/action.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert action["inputs"]["enable_dependency_cache"]["default"] == "false"
    opt_in = "inputs.enable_cache == 'true' && inputs.enable_dependency_cache == 'true'"
    for step in action["runs"]["steps"]:
        uses = step.get("uses", "")
        if "setup-uv@" in uses:
            assert step["if"] == opt_in
            assert step["with"]["enable-cache"] == "${{ inputs.enable_cache }}"
            assert "uv.lock" in step["with"]["cache-dependency-glob"]
            versions = dict(
                line.split()
                for line in (ROOT / ".tool-versions").read_text().splitlines()
                if line and not line.startswith("#")
            )
            assert versions["uv"] == step["with"]["version"]
        elif "actions/cache@" in uses:
            assert step["if"] == (
                opt_in
                if "package-downloads" in step["with"]["key"]
                else "inputs.enable_cache == 'true'"
            )
            assert "lock" in step["with"]["key"]
            assert "restore-keys" not in step["with"]


@pytest.mark.parametrize(
    "file,function,argument",
    [
        ("tests/conftest.py", "pytest_sessionstart", "session"),
        ("tests/integration/conftest.py", "pytest_configure", "config"),
    ],
)
def test_collection_does_not_start_external_services(file, function, argument):
    # Execute just the hook definition, with no application import or settings.
    module = ast.parse((ROOT / file).read_text())
    definition = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[definition], type_ignores=[]), file, "exec"), namespace
    )
    config = SimpleNamespace(option=SimpleNamespace(collectonly=True))
    namespace[function](
        SimpleNamespace(config=config) if argument == "session" else config
    )
