from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/consume_staging_rehearsal_base_state_refresh_plan.py"
SOURCE = "a" * 40
PLAN_DIGEST = "b" * 64


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("state_refresh_consumer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Exercise the bootstrap with the package-qualified dependency already loaded.
importlib.import_module("scripts.validate_staging_rehearsal_base_recovery_plan")
consumer = _load_module()


def _receipt(tofu: Path) -> dict[str, Any]:
    return {
        "policy_id": "cosmaneura-staging-rehearsal-base-state-refresh-plan-v1",
        "generation_mode": "digest_bound_revalidation",
        "source_commit": SOURCE,
        "source_tree_sha256": "f" * 64,
        "state_json_sha256": consumer.refresh.EXPECTED_PRE_REFRESH_STATE_SHA256,
        "saved_plan_sha256": PLAN_DIGEST,
        "state_lineage": consumer.recovery.EXPECTED_LINEAGE,
        "pre_refresh_state_serial": 8,
        "expected_post_refresh_state_serial": 9,
        "state_refresh_count": 10,
        "azure_resource_action_count": 0,
        "output_create_count": 1,
        "apply_authorized": False,
        "founder_apply_required": True,
        "pass": True,
        "open_tofu_path": str(tofu),
        "open_tofu_sha256": consumer._digest(tofu.read_bytes()),
        "execution_provider_tree_sha256": "c" * 64,
        "backend_metadata_sha256": "d" * 64,
    }


def test_cli_bootstraps_under_isolated_python() -> None:
    result = subprocess.run(
        [sys.executable, "-IS", str(SCRIPT), "--help"],
        cwd=ROOT,
        env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"--expected-saved-plan-sha256" in result.stdout


def test_open_pinned_plan_binds_descriptor_and_digest(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    plan = tmp_path / "refresh.tfplan"
    plan.write_bytes(b"reviewed-plan")
    plan.chmod(0o400)
    descriptor = consumer._open_pinned_plan(
        plan, expected_sha256=consumer._digest(b"reviewed-plan")
    )
    try:
        assert os.read(descriptor, 32) == b"reviewed-plan"
        assert os.get_inheritable(descriptor)
    finally:
        os.close(descriptor)
    with pytest.raises(consumer.ConsumerError, match="saved_plan.changed"):
        consumer._open_pinned_plan(plan, expected_sha256="0" * 64)


def test_revalidate_pinned_plan_detects_late_change(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    plan = tmp_path / "refresh.tfplan"
    plan.write_bytes(b"reviewed-plan")
    plan.chmod(0o400)
    descriptor = os.open(plan, os.O_RDONLY)
    try:
        consumer._revalidate_pinned_plan(
            descriptor, expected_sha256=consumer._digest(b"reviewed-plan")
        )
        plan.chmod(0o600)
        with pytest.raises(consumer.ConsumerError, match="saved_plan.changed"):
            consumer._revalidate_pinned_plan(
                descriptor, expected_sha256=consumer._digest(b"reviewed-plan")
            )
    finally:
        os.close(descriptor)


def test_claim_is_bounded_owner_only_and_one_use(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    facts = {"policy_id": "test", "apply_outcome": "unknown"}
    consumer._claim(tmp_path, facts)
    claim = tmp_path / consumer.CLAIM_NAME
    assert claim.read_bytes() == consumer._canonical(facts) + b"\n"
    assert claim.stat().st_mode & 0o777 == 0o400
    with pytest.raises(consumer.ConsumerError, match="claim.exists_or_unavailable"):
        consumer._claim(tmp_path, facts)
    with pytest.raises(consumer.ConsumerError, match="claim.invalid"):
        consumer._claim(tmp_path / "other", {"value": "x" * consumer.MAX_CLAIM_BYTES})


def test_confirmation_requires_exact_state_only_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        (
            SOURCE,
            PLAN_DIGEST,
            consumer.CHANGE_STATEMENT,
            "APPLY 0001020304050607",
        )
    )
    prompts: list[str] = []
    monkeypatch.setattr(consumer, "_validate_operator_tty", lambda: 91)
    monkeypatch.setattr(os, "close", lambda _: None)
    monkeypatch.setattr(
        consumer,
        "_read_prompt",
        lambda _fd, prompt, _deadline: prompts.append(prompt) or next(responses),
    )
    monkeypatch.setattr(consumer.time, "monotonic", lambda: 100.0)
    deadline = consumer._confirm_operator(
        SOURCE, PLAN_DIGEST, challenge_source=lambda _: bytes(range(8))
    )
    assert deadline == 220.0
    assert len(prompts) == 4


def test_execution_environment_is_closed_and_exact(tmp_path: Path) -> None:
    result = consumer._execution_environment(
        home=Path("/Users/operator"),
        azure_config=Path("/Users/operator/.azure"),
        azure_cli=Path("/trusted/bin/az"),
        execution_dir=tmp_path,
    )
    assert result["PATH"].startswith("/trusted/bin:")
    assert result["TF_DATA_DIR"] == str(tmp_path)
    assert result["ARM_SUBSCRIPTION_ID"] == consumer.recovery.EXPECTED_SUBSCRIPTION_ID
    assert not any("PROXY" in name for name in result)


@pytest.mark.parametrize(
    "name",
    (
        "PYTHONPATH",
        "TF_CLI_ARGS",
        "TOFU_CONFIG_FILE",
        "OPENTOFU_CONFIG_FILE",
        "GIT_CONFIG_GLOBAL",
        "ARM_CLIENT_ID",
        "AZURE_CONFIG_DIR",
        "HTTPS_PROXY",
        "http_proxy",
        "DYLD_INSERT_LIBRARIES",
    ),
)
def test_ambient_environment_overrides_are_refused(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for existing in tuple(os.environ):
        monkeypatch.delenv(existing)
    monkeypatch.setattr(consumer.sys, "prefix", consumer.sys.base_prefix)
    monkeypatch.setattr(consumer.sys, "gettrace", lambda: None)
    monkeypatch.setattr(consumer.sys, "getprofile", lambda: None)
    consumer._reject_ambient_environment()
    monkeypatch.setenv(name, "unsafe")
    with pytest.raises(consumer.ConsumerError, match="environment.unsafe"):
        consumer._reject_ambient_environment()


def test_consume_execs_only_exact_reviewed_refresh_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    plan = tmp_path / "refresh.tfplan"
    state = tmp_path / "serial-8.tfstate"
    execution = tmp_path / "execution"
    tofu = tmp_path / "tofu"
    for path, value, mode in (
        (plan, b"plan", 0o400),
        (state, b'{"serial":8}', 0o600),
        (tofu, b"tofu", 0o700),
    ):
        path.write_bytes(value)
        path.chmod(mode)
    execution.mkdir(mode=0o700)
    receipt = _receipt(tofu)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(consumer, "_reject_ambient_environment", lambda: None)
    monkeypatch.setattr(consumer, "_python_runtime_sha256", lambda: "9" * 64)
    monkeypatch.setattr(
        consumer,
        "_operator_paths",
        lambda: (Path("/Users/operator"), Path("/Users/operator/.azure")),
    )
    monkeypatch.setattr(consumer, "_resolve_azure_cli", lambda: Path("/trusted/bin/az"))
    monkeypatch.setattr(
        consumer, "_pinned_process_environment", lambda **_: nullcontext()
    )
    monkeypatch.setattr(
        consumer.refresh, "revalidate_state_refresh_plan", lambda **_: receipt
    )
    monkeypatch.setattr(consumer, "_consumer_source_digest", lambda _: "e" * 64)
    monkeypatch.setattr(
        consumer.refresh,
        "_source_snapshot",
        lambda _: consumer.recovery.SourceSnapshot(SOURCE, {}, b"lock", "f" * 64),
    )
    monkeypatch.setattr(consumer, "_confirm_operator", lambda *_args, **_kwargs: 200.0)
    monkeypatch.setattr(consumer.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        consumer.refresh, "_pull_remote_state", lambda **_: state.read_bytes()
    )
    monkeypatch.setattr(
        consumer.recovery,
        "_load_json_bytes",
        lambda value, **_: {"serial": 8} if value == state.read_bytes() else {},
    )
    monkeypatch.setattr(
        consumer.refresh, "_state_binding_projection", lambda value: value
    )
    monkeypatch.setattr(
        consumer.refresh, "_validate_pre_refresh_state_digest", lambda _: None
    )
    descriptor = os.open(plan, os.O_RDONLY)
    monkeypatch.setattr(
        consumer, "_open_pinned_plan", lambda *_args, **_kwargs: descriptor
    )
    monkeypatch.setattr(consumer.recovery, "_resolve_and_verify_tofu", lambda: tofu)
    monkeypatch.setattr(consumer, "_validate_path_ancestry", lambda _: None)
    monkeypatch.setattr(
        consumer.recovery,
        "_executable_sha256",
        lambda path, **_: (
            "8" * 64
            if path == Path("/trusted/bin/az")
            else consumer._digest(path.read_bytes())
        ),
    )
    monkeypatch.setattr(consumer.recovery, "_provider_tree_sha256", lambda _: "c" * 64)
    monkeypatch.setattr(consumer.recovery, "_backend_snapshot", lambda **_: "d" * 64)
    monkeypatch.setattr(
        consumer, "_validate_operator_tty", lambda: os.open(os.devnull, os.O_RDONLY)
    )
    monkeypatch.setattr(
        consumer, "_revalidate_pinned_plan", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        consumer, "_claim", lambda directory, facts: captured.update(claim=facts)
    )

    class Executed(Exception):
        pass

    def execve(path: str, argv: list[str], environment: dict[str, str]) -> None:
        captured.update(path=path, argv=argv, environment=environment)
        raise Executed

    monkeypatch.setattr(os, "execve", execve)
    with pytest.raises(Executed):
        consumer.consume(
            saved_plan=plan,
            state_json=state,
            execution_dir=execution,
            expected_source_commit=SOURCE,
            expected_saved_plan_sha256=PLAN_DIGEST,
        )
    assert captured["argv"][-1] == f"/dev/fd/{descriptor}"
    assert captured["argv"][2:4] == ["apply", "-input=false"]
    assert captured["claim"]["apply_outcome"] == "unknown"
    assert captured["claim"]["python_runtime_sha256"] == "9" * 64
    assert captured["claim"]["azure_cli_sha256"] == "8" * 64
    assert captured["environment"]["TF_DATA_DIR"] == str(execution)


def test_consumer_source_has_no_subprocess_apply_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.splitlines()[0] == "#!/opt/homebrew/bin/python3 -IS"
    assert SCRIPT.stat().st_mode & 0o111
    assert "subprocess" not in source
    assert "os.execve" in source
    assert "--auto-approve" not in source
    assert "-refresh=false" not in source
    assert "-refresh-only" not in source


def test_main_redacts_argument_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert consumer.main(["--saved-pl", "secret-plan-path"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "state_refresh_apply_refused\n"
    assert "secret" not in captured.err
