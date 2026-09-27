"""Execute migration shell/CLI boundaries against a synthetic Azure provider."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = (ROOT / "infra-staging/deploy.sh").read_text()
BOUND = ROOT / "scripts/staging_azure_cli_bound.py"
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
GROUP = "cosmaneura-staging"
JOB = "cn-stg-mig-" + "a" * 20
OTHER_JOB = "cn-stg-mig-" + "b" * 20
LEGACY_JOB = "cosmaneura-staging-migrate"

# This substitutes only credential acquisition and the final Azure SDK call.
# The real bound CLI main() still parses context and validates every argument.
# All provider state, argv and credentials here are fictional, local test data.
FAKE_BOUND = r"""
import importlib.util,json,os,sys,time,types
from pathlib import Path
spec=importlib.util.spec_from_file_location("tested_bound",os.environ["TEST_BOUND_SOURCE"])
bound=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bound)
bound.token_record=lambda *args: ("synthetic-management-token",int(time.time())+3600)
for name in ("azure","azure.cli","azure.cli.core","azure.cli.core._profile","azure.core","azure.core.credentials"):
    sys.modules[name]=types.ModuleType(name)
sys.modules["azure.cli.core._profile"].Profile=type("Profile",(),{})
sys.modules["azure.core.credentials"].AccessToken=tuple
sys.modules["azure.core.credentials"].AccessTokenInfo=tuple
def value(args,*flags):
    return next((args[args.index(flag)+1] for flag in flags if flag in args),None)
def values(args,flag):
    if flag not in args: return []
    result=[]
    for arg in args[args.index(flag)+1:]:
        if arg.startswith("-"): break
        result.append(arg)
    return result
def invoke(args):
    context=value(sys.argv,"--migration-job-name")
    directory=Path(os.environ["TEST_PROVIDER"])
    with (directory/"calls.jsonl").open("a") as stream:
        stream.write(json.dumps({"context":context,"argv":args})+"\n")
    state_file=directory/"state.json"
    state=json.loads(state_file.read_text())
    mode=os.environ.get("TEST_MODE", "success")
    name=value(args,"-n","--name")
    query=value(args,"--query")
    operation=tuple(args[:4] if args[2] in {"execution","logs"} else args[:3])
    output=None
    status=0
    if operation==("containerapp","job","list"):
        if mode=="list-failure": return 7
        target=query.split("'")[1]
        output=int(target in state["jobs"] or mode=="collision")
    elif operation==("containerapp","job","create"):
        if name in state["jobs"]: return 8
        if mode!="create-failure":
            state["jobs"][name]={"create":args,"executions":{}}
        if mode in {"create-failure","ambiguous-create"}: status=7
    elif operation==("containerapp","job","show"):
        job=state["jobs"].get(name)
        if job is None: return 3
        if query=="properties.template.containers[].name": output=name
        elif query:
            created=job["create"]
            env=[]
            for item in values(created,"--env-vars"):
                key,entry=item.split("=",1)
                env.append({"name":key,"secretRef":entry.removeprefix("secretref:") if entry.startswith("secretref:") else None})
            output={"containers":[{"name":name,"image":value(created,"--image"),"command":[],"args":values(created,"--args"),"env":env}],"secretNames":[entry.split("=",1)[0] for entry in values(created,"--secrets")]}
        else: output={}
    elif operation==("containerapp","job","start"):
        job=state["jobs"][name]
        number=len(job["executions"])+1
        execution=name+"-"+str(number)
        kind="schema" if any(item.startswith("--args=-c") for item in args) else "preflight" if "--args" in args else "migration"
        job["executions"][execution]={"kind":kind,"status":"Running" if mode=="timeout" and kind=="migration" else "Succeeded","schema":"111111111111" if number==1 else "222222222222"}
        output=execution
    elif operation==("containerapp","job","execution","show"):
        execution=value(args,"--job-execution-name")
        output=state["jobs"][name]["executions"][execution]["status"]
    elif operation==("containerapp","job","logs","show"):
        execution=value(args,"--execution")
        output="STAGING_SCHEMA_REVISION="+state["jobs"][name]["executions"][execution]["schema"]
    elif operation==("containerapp","job","stop"):
        execution=value(args,"--job-execution-name")
        state["jobs"][name]["executions"][execution]["status"]="Stopped"
    elif operation==("containerapp","job","delete"):
        if mode=="cleanup-failure": status=7
        else: del state["jobs"][name]
    state_file.write_text(json.dumps(state))
    if output is not None: print(output if isinstance(output,str) else json.dumps(output))
    return status
sys.modules["azure.cli.core"].get_default_cli=lambda: types.SimpleNamespace(invoke=invoke)
try:
    raise SystemExit(bound.main())
except bound.BoundAzureRefused:
    print("staging Azure command binding refused",file=sys.stderr)
    raise SystemExit(2)
"""


def _function(name: str) -> str:
    start = DEPLOY.index(name + "() {")
    return DEPLOY[start : DEPLOY.index("\n}", start) + 2]


def _provider(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bootstrap = tmp_path / "fake_bound.py"
    bootstrap.write_text(FAKE_BOUND)
    state = tmp_path / "state.json"
    if not state.exists():
        state.write_text(json.dumps({"jobs": {LEGACY_JOB: {}, OTHER_JOB: {}}}))
    env = {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "TEST_BOUND_SOURCE": str(BOUND),
        "TEST_PROVIDER": str(tmp_path),
    }
    return bootstrap, env


def _calls(tmp_path: Path) -> list[dict]:
    path = tmp_path / "calls.jsonl"
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def _value(args: list[str], *flags: str) -> str | None:
    return next((args[args.index(flag) + 1] for flag in flags if flag in args), None)


def _run(
    tmp_path: Path, mode: str = "success", *, release_v2: bool = True
) -> tuple[subprocess.CompletedProcess[str], str, list[dict]]:
    bootstrap, env = _provider(tmp_path)
    before = len(_calls(tmp_path))
    token_provider = tmp_path / "synthetic_token_provider"
    token_provider.write_text("#!/bin/sh\nprintf '{}\\n'\n")
    token_provider.chmod(0o700)
    functions = "\n".join(
        _function(name)
        for name in (
            "initialize_migration_job",
            "az",
            "cleanup_migration_job",
            "capture_failed_workload_state",
            "verify_migration_job_template",
            "wait_for_migration_job_execution",
            "capture_schema_revision",
            "finish_migration_attempt",
            "run_migrations",
        )
    )
    initializer = DEPLOY[
        DEPLOY.index("\ninitialize_migration_job || exit 1") : DEPLOY.index(
            "\nAZ_EXECUTABLE="
        )
    ]
    migration_gate = DEPLOY[
        DEPLOY.index("# Run migrations before deploying new containers") : DEPLOY.index(
            "# Deploy and verify recovery workloads"
        )
    ]
    script = f"""
set -u
MIGRATION_JOB=""
MIGRATION_JOB_CLEANUP_REQUIRED=false
WORKLOAD_CAPTURE_READY=false
PYTHON_BIN={shlex.quote(sys.executable)}
AZURE_CLI_PYTHON=$PYTHON_BIN
BOUND_AZURE_CLI={shlex.quote(str(bootstrap))}
AZ_EXECUTABLE={shlex.quote(str(token_provider))}
SCRIPT_DIR={shlex.quote(str(ROOT / "infra-staging"))}
RESOURCE_GROUP={GROUP}
PINNED_STAGING_SUBSCRIPTION_ID={SUBSCRIPTION}
STAGING_DEPLOYMENT_IDENTITY_MODE=founder-user
GIT_SHA=866bd4bc01e0e7be32c59eccdaf9dac3f495c7de
FOUNDER_CANDIDATE_SHA=$GIT_SHA
STAGING_IMAGE_DIGEST=sha256:{"a" * 64}
FOUNDER_CANDIDATE_IMAGE_DIGEST=$STAGING_IMAGE_DIGEST
STAGING_IMAGE_REFERENCE=cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@$STAGING_IMAGE_DIGEST
ENVIRONMENT_NAME=cosmaneura-staging-app-environment
ACR_SERVER=cosmaneurastagingregistry.azurecr.io
ACR_USERNAME=synthetic
ACR_PASSWORD=synthetic-registry-password
ALEMBIC_COMPAT_DATABASE_SECRET_NAME=database-url-alembic-compat
COMMON_SECRET_VARS=(DATABASE_URL)
PUBLIC_VARS=(AUTO_MIGRATE_ON_STARTUP)
ENV_JSON='{{"DATABASE_URL":"postgresql://synthetic:synthetic-only@db.example.test/staging","AUTO_MIGRATE_ON_STARTUP":"false"}}'
RELEASE_V2_JSON={shlex.quote('{"version":2}' if release_v2 else "")}
EXPECTED_SCHEMA_BEFORE=111111111111
EXPECTED_SCHEMA_AFTER=222222222222
sleep() {{ :; }}
emit_github_output() {{ printf 'EVIDENCE %s=%s\\n' "$1" "$2"; }}
verify_founder_release_source() {{ :; }}
verify_azure_deployment_identity() {{ :; }}
verify_preserved_runtime() {{ :; }}
{functions}
{initializer}
first_job=$MIGRATION_JOB
initialize_migration_job || exit 1
[[ "$first_job" == "$MIGRATION_JOB" ]] || exit 1
printf 'OWNED_JOB=%s\\n' "$MIGRATION_JOB"
trap capture_failed_workload_state EXIT
{migration_gate}
printf 'WORKLOAD_ROLLOUT_ALLOWED\\n'
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={**env, "TEST_MODE": mode},
        capture_output=True,
        text=True,
        check=False,
        timeout=40,
    )
    match = re.search(r"^OWNED_JOB=(cn-stg-mig-[0-9a-f]{20})$", result.stdout, re.M)
    assert match, result.stdout + result.stderr
    assert "synthetic-only" not in result.stdout + result.stderr
    assert "synthetic-registry-password" not in result.stdout + result.stderr
    return result, match[1], _calls(tmp_path)[before:]


def _assert_owned(calls: list[dict], name: str) -> None:
    assert calls
    for call in calls:
        assert call["context"] == name
        args = call["argv"]
        assert _value(args, "--subscription") == SUBSCRIPTION
        assert _value(args, "-g", "--resource-group") == GROUP
        if args[:3] == ["containerapp", "job", "list"]:
            assert _value(args, "--query") == f"[?name=='{name}'] | length(@)"
        else:
            assert _value(args, "-n", "--name") == name
        for flag in ("--container", "--container-name"):
            if flag in args:
                assert _value(args, flag) == name
        for flag in ("--execution", "--job-execution-name"):
            if flag in args:
                assert re.fullmatch(
                    re.escape(name) + r"-[a-z0-9]+", _value(args, flag) or ""
                )


@pytest.mark.parametrize("release_v2", [False, True])
def test_two_invocations_keep_distinct_jobs_and_preserve_unrelated_jobs(
    tmp_path: Path, release_v2: bool
) -> None:
    first, first_name, first_calls = _run(tmp_path, release_v2=release_v2)
    second, second_name, second_calls = _run(tmp_path, release_v2=release_v2)
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first_name != second_name
    for result, name, calls in (
        (first, first_name, first_calls),
        (second, second_name, second_calls),
    ):
        _assert_owned(calls, name)
        assert f"Created invocation migration job: {name}" in result.stdout
        assert "EVIDENCE schema_before=111111111111" in result.stdout
        assert "EVIDENCE schema_after=222222222222" in result.stdout
        assert "WORKLOAD_ROLLOUT_ALLOWED" in result.stdout
        creates = [
            call
            for call in calls
            if call["argv"][:3] == ["containerapp", "job", "create"]
        ]
        assert len(creates) == 1
        command = _value(creates[0]["argv"], "--args")
        assert command is not None
        assert (command == "/opt/venv/bin/alembic upgrade head") is release_v2
        assert (
            sum(call["argv"][:3] == ["containerapp", "job", "start"] for call in calls)
            == 4
        )
        assert (
            sum(call["argv"][:3] == ["containerapp", "job", "delete"] for call in calls)
            == 1
        )
    assert set(json.loads((tmp_path / "state.json").read_text())["jobs"]) == {
        LEGACY_JOB,
        OTHER_JOB,
    }


@pytest.mark.parametrize(
    "mode", ["collision", "list-failure", "create-failure", "ambiguous-create"]
)
def test_collision_and_unconfirmed_create_never_claim_cleanup(
    tmp_path: Path, mode: str
) -> None:
    result, name, calls = _run(tmp_path, mode)
    assert result.returncode != 0
    assert "WORKLOAD_ROLLOUT_ALLOWED" not in result.stdout
    assert "Created invocation migration job:" not in result.stdout
    _assert_owned(calls, name)
    operations = [call["argv"][:3] for call in calls]
    assert ["containerapp", "job", "delete"] not in operations
    assert ["containerapp", "job", "start"] not in operations
    assert operations.count(["containerapp", "job", "create"]) == int(
        mode in {"create-failure", "ambiguous-create"}
    )
    jobs = json.loads((tmp_path / "state.json").read_text())["jobs"]
    assert (name in jobs) is (mode == "ambiguous-create")
    assert {LEGACY_JOB, OTHER_JOB}.issubset(jobs)


def test_timeout_stops_and_observes_only_owned_execution_before_cleanup(
    tmp_path: Path,
) -> None:
    result, name, calls = _run(tmp_path, "timeout")
    assert result.returncode != 0
    assert "WORKLOAD_ROLLOUT_ALLOWED" not in result.stdout
    assert "EVIDENCE schema_after=222222222222" in result.stdout
    _assert_owned(calls, name)
    stops = [
        call for call in calls if call["argv"][:3] == ["containerapp", "job", "stop"]
    ]
    assert len(stops) == 1
    assert _value(stops[0]["argv"], "--job-execution-name") == name + "-3"
    assert any(call["argv"][:3] == ["containerapp", "job", "delete"] for call in calls)
    assert name not in json.loads((tmp_path / "state.json").read_text())["jobs"]


def test_cleanup_failure_blocks_rollout_and_keeps_other_jobs(tmp_path: Path) -> None:
    result, name, calls = _run(tmp_path, "cleanup-failure")
    assert result.returncode != 0
    assert "transient job cleanup failed; workloads were not updated" in result.stdout
    assert "WORKLOAD_ROLLOUT_ALLOWED" not in result.stdout
    _assert_owned(calls, name)
    jobs = json.loads((tmp_path / "state.json").read_text())["jobs"]
    assert set(jobs) == {LEGACY_JOB, OTHER_JOB, name}


def test_later_invocation_preserves_an_earlier_ambiguous_create(tmp_path: Path) -> None:
    earlier, retained_name, earlier_calls = _run(tmp_path, "ambiguous-create")
    later, later_name, later_calls = _run(tmp_path)
    assert earlier.returncode != 0 and later.returncode == 0
    assert retained_name != later_name
    _assert_owned(earlier_calls, retained_name)
    _assert_owned(later_calls, later_name)
    assert set(json.loads((tmp_path / "state.json").read_text())["jobs"]) == {
        LEGACY_JOB,
        OTHER_JOB,
        retained_name,
    }


def _cli(
    tmp_path: Path, args: list[str], context: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    bootstrap, env = _provider(tmp_path)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(bootstrap),
            *(
                context
                if context is not None
                else ["--candidate-sha", "c" * 40, "--migration-job-name", JOB]
            ),
            *args,
            "--subscription",
            SUBSCRIPTION,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize(
    "name",
    [
        OTHER_JOB,
        LEGACY_JOB,
        "cosmaneura-staging-app",
        "cosmaneura-staging-worker",
        "cosmaneura-staging-scheduler",
        "cosmaneura-staging-scribe-api",
        "cosmaneura-staging-scribe-native",
        "cosmaneuraprod-app",
    ],
)
@pytest.mark.parametrize("operation", ["create", "delete", "show", "start"])
def test_cli_refuses_every_foreign_job_name(
    tmp_path: Path, name: str, operation: str
) -> None:
    result = _cli(
        tmp_path, ["containerapp", "job", operation, "--name", name, "-g", GROUP]
    )
    assert result.returncode == 2
    assert result.stderr == "staging Azure command binding refused\n"
    assert not _calls(tmp_path)


@pytest.mark.parametrize(
    "args",
    [
        ["list", "--query", f"[?name=='{OTHER_JOB}'] | length(@)", "-o", "tsv"],
        ["list", "--query", "[].name", "-o", "tsv"],
        ["show", "--name", JOB, "--query", "properties.configuration.secrets"],
        ["stop", "--name", JOB, "--job-execution-name", OTHER_JOB + "-1"],
        ["execution", "show", "--name", JOB, "--job-execution-name", JOB + "-other-1"],
        ["logs", "show", "--name", JOB, "--execution", LEGACY_JOB + "-1"],
        [
            "logs",
            "show",
            "--name",
            JOB,
            "--execution",
            JOB + "-1",
            "--container",
            OTHER_JOB,
        ],
        ["start", "--name", JOB, "--container-name", "cosmaneura-staging-app"],
        ["start", "--name", JOB, "--execution", JOB + "-1"],
        ["show", "--name", JOB, "-n", OTHER_JOB],
    ],
)
def test_cli_refuses_cross_job_queries_containers_and_execution_selectors(
    tmp_path: Path, args: list[str]
) -> None:
    result = _cli(tmp_path, ["containerapp", "job", *args, "-g", GROUP])
    assert result.returncode == 2
    assert not _calls(tmp_path)


@pytest.mark.parametrize(
    "name",
    [JOB, OTHER_JOB, LEGACY_JOB, "cosmaneura-staging-scribe-api", "cosmaneuraprod-app"],
)
def test_job_context_does_not_expand_app_mutation_allowlist(
    tmp_path: Path, name: str
) -> None:
    result = _cli(tmp_path, ["containerapp", "update", "-n", name, "-g", GROUP])
    assert result.returncode == 2
    assert not _calls(tmp_path)


@pytest.mark.parametrize(
    "context",
    [
        ["--migration-job-name", LEGACY_JOB],
        ["--migration-job-name", "cn-stg-mig-" + "a" * 19],
        ["--migration-job-name", "cn-stg-mig-" + "A" * 20],
        ["--migration-job-name", JOB + "' || true"],
        ["--candidate-sha", "c" * 39, "--migration-job-name", JOB],
        ["--migration-job-name", JOB, "--migration-job-name", OTHER_JOB],
    ],
)
def test_cli_refuses_invalid_or_duplicate_context(
    tmp_path: Path, context: list[str]
) -> None:
    result = _cli(
        tmp_path, ["containerapp", "job", "show", "--name", JOB, "-g", GROUP], context
    )
    assert result.returncode == 2
    assert not _calls(tmp_path)


def test_legacy_cli_default_remains_scoped_to_legacy_job(tmp_path: Path) -> None:
    result = _cli(
        tmp_path, ["containerapp", "job", "show", "--name", LEGACY_JOB, "-g", GROUP], []
    )
    assert result.returncode == 0
    assert _calls(tmp_path)[0]["context"] is None
    result = _cli(
        tmp_path, ["containerapp", "job", "show", "--name", JOB, "-g", GROUP], []
    )
    assert result.returncode == 2
    assert len(_calls(tmp_path)) == 1
