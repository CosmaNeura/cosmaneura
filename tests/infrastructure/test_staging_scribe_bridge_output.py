"""Execute the real bridge/emitter without a deployer, resolver or cloud child."""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def function(name: str) -> str:
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", source, re.MULTILINE | re.DOTALL)
    assert match is not None
    return match.group()


def run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", "set -euo pipefail\n" + script],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin"},
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    "name",
    [
        "source_sha",
        "image_digest",
        "configuration_evidence",
        "schema_before",
        "schema_after",
        "recovery_digest",
        "workloads_before",
        "workloads_after",
        "scribe_initialization_result",
    ],
)
def test_actual_emitter_accepts_only_declared_metadata(
    name: str, tmp_path: Path
) -> None:
    target = tmp_path / "output"
    value = '{"workload":"synthetic","target_sha256":"fixture"}'
    result = run(
        function("emit_github_output")
        + "\nGITHUB_OUTPUT="
        + shlex.quote(str(target))
        + "\nemit_github_output "
        + shlex.quote(name)
        + " "
        + shlex.quote(value)
    )
    assert result.returncode == 0
    assert target.read_text() == f"{name}={value}\n"


@pytest.mark.parametrize(
    "name", ["unexpected_output", "scribe_initialization_result_extra"]
)
def test_actual_emitter_refuses_unknown_names(name: str, tmp_path: Path) -> None:
    target = tmp_path / "output"
    result = run(
        function("emit_github_output")
        + "\nGITHUB_OUTPUT="
        + shlex.quote(str(target))
        + "\nemit_github_output "
        + shlex.quote(name)
        + " fixture"
    )
    assert result.returncode != 0
    assert not target.exists()


@pytest.mark.parametrize("value", ["first\nsecond", "first\rsecond"])
def test_actual_emitter_refuses_multiline_metadata(value: str, tmp_path: Path) -> None:
    target = tmp_path / "output"
    result = run(
        function("emit_github_output")
        + "\nGITHUB_OUTPUT="
        + shlex.quote(str(target))
        + "\nemit_github_output scribe_initialization_result "
        + shlex.quote(value)
    )
    assert result.returncode != 0
    assert not target.exists()


@pytest.mark.parametrize("action", ["before", "step", "closed-step", "finish"])
def test_actual_bridge_returns_its_single_line_receipt(
    action: str, tmp_path: Path
) -> None:
    # Only the executor child is synthetic. Both actual shell functions run
    # unchanged; no configuration resolver, journal, migration or cloud is used.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    receipt = {"workload": "synthetic", "target_sha256": "fixture"}
    (scripts / "staging_scribe_initialization_executor.py").write_text(
        "import json,sys\n"
        "assert json.load(sys.stdin) == {'configuration': {'synthetic': 'fixture'}}\n"
        f"assert sys.argv[1] == {action!r}\n"
        f"print(json.dumps({receipt!r},sort_keys=True))\n"
    )
    target = tmp_path / "output"
    bindings = {
        "ENV_JSON": '{"synthetic":"fixture"}',
        "REPOSITORY_ROOT": str(tmp_path),
        "PYTHON_BIN": sys.executable,
        "GITHUB_OUTPUT": str(target),
        "STAGING_SCRIBE_INIT_PLAN": "fixture",
        "EXPECTED_STAGING_SCRIBE_INIT_PLAN_SHA256": "fixture",
        "STAGING_BUILD_PLAN": "fixture",
        "EXPECTED_STAGING_BUILD_PLAN_SHA256": "fixture",
        "STAGING_PUBLICATION_RECEIPT": "fixture",
        "EXPECTED_STAGING_PUBLICATION_RECEIPT_SHA256": "fixture",
        "EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256": "fixture",
        "STAGING_SCRIBE_INIT_JOURNAL": "fixture",
        "EXPECTED_STAGING_EFFECTIVE_CONFIGURATION_SHA256": "fixture",
    }
    script = function("emit_github_output") + "\n" + function("staging_scribe_bridge")
    script += "\n" + "\n".join(
        key + "=" + shlex.quote(value) for key, value in bindings.items()
    )
    result = run(script + "\nstaging_scribe_bridge " + shlex.quote(action) + " fixture")
    assert result.returncode == 0
    assert (
        target.read_text()
        == "scribe_initialization_result=" + json.dumps(receipt, sort_keys=True) + "\n"
    )
