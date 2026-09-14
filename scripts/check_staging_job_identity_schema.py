"""Check the pinned Jobs identity schema without a backend or Azure operations.

Only provider installation and local ``tofu validate`` are invoked. The fixture
is deliberately synthetic, incomplete for execution, and absent from the real
root module. No plan/apply/preflight, credential forwarding or caller command.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "infra-staging-rehearsal"
FIXTURE = ROOT / "schema-fixtures/job-identity-none/main.tf"


def _run(
    binary: str, directory: Path, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    # No HOME, Azure/ARM identity, token, proxy, TF_CLI_ARGS, CLI config or plugin
    # override environment is forwarded. The absolute binary is resolved once.
    environment = {
        "PATH": os.defpath,
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
        "TF_DATA_DIR": str(directory / "provider-data"),
    }
    return subprocess.run(
        [binary, f"-chdir={directory}", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _validation(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        report = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("schema checker did not receive validation JSON") from exc
    if type(report) is not dict:
        raise RuntimeError("schema checker validation result is not an object")
    return report


def main() -> None:
    binary = shutil.which("tofu")
    if binary is None:
        raise RuntimeError("OpenTofu 1.12.5 is required")
    original = FIXTURE.read_text(encoding="utf-8")
    marker = 'lifecycle = "None"'
    if original.count(marker) != 1:
        raise RuntimeError("expected one exact synthetic lifecycle field")
    if "schema_validation_enabled = true" not in original:
        raise RuntimeError("fixture must retain provider schema validation")
    lock = (ROOT / ".terraform.lock.hcl").read_bytes()
    # Reuse only packages from this root's preceding backend-disabled init.
    # OpenTofu still verifies them against the exact readonly lock; no download,
    # unpinned plugin directory or caller/environment-selected mirror is used.
    provider_mirror = (ROOT / ".terraform/providers").resolve()
    if not provider_mirror.is_dir():
        raise RuntimeError("initialize the staging root with backend=false first")
    # This is the checker's own disposable fixture, not an operator checkout,
    # backend, plan packet, state directory or evidence/claim directory.
    with tempfile.TemporaryDirectory(
        prefix="staging-job-identity-schema-"
    ) as temporary:
        directory = Path(temporary)
        (directory / "main.tf").write_text(
            # The upstream enum includes a string fallback; do not assume an
            # unknown string is a reliable negative schema control.
            # A boolean is the negative control for active TYPE validation.
            original.replace(marker, "lifecycle = true"),
            encoding="utf-8",
        )
        (directory / ".terraform.lock.hcl").write_bytes(lock)
        version = _run(binary, directory, ["version", "-json"])
        if (
            version.returncode != 0
            or json.loads(version.stdout).get("terraform_version") != "1.12.5"
        ):
            raise RuntimeError("exact OpenTofu 1.12.5 is required")
        initialized = _run(
            binary,
            directory,
            [
                "init",
                "-backend=false",
                "-input=false",
                "-lockfile=readonly",
                "-no-color",
                f"-plugin-dir={provider_mirror}",
            ],
        )
        if initialized.returncode != 0:
            # Only a synthetic fixture and public provider-install diagnostics;
            # no credential environment or live Azure/state output is involved.
            diagnostic = (initialized.stdout + initialized.stderr)[-6000:]
            raise RuntimeError(
                f"schema-only provider initialization failed: {diagnostic}"
            )
        invalid = _run(binary, directory, ["validate", "-json"])
        rejected = _validation(invalid)
        errors = [
            item
            for item in rejected.get("diagnostics", [])
            if type(item) is dict and item.get("severity") == "error"
        ]
        if (
            invalid.returncode == 0
            or rejected.get("valid") is not False
            or not any(
                "properties.configuration.identitySettings.0.lifecycle"
                in str(item.get("detail", ""))
                and "embedded schema validation failed" in str(item.get("detail", ""))
                for item in errors
            )
        ):
            diagnostic = json.dumps(rejected, sort_keys=True)[:6000]
            raise RuntimeError(
                f"boolean lifecycle did not fail at its schema field: {diagnostic}"
            )
        (directory / "main.tf").write_text(original, encoding="utf-8")
        valid = _run(binary, directory, ["validate", "-json"])
        accepted = _validation(valid)
        if (
            valid.returncode != 0
            or accepted.get("valid") is not True
            or accepted.get("error_count") != 0
        ):
            diagnostic = json.dumps(accepted, sort_keys=True)[:6000]
            raise RuntimeError(f"lifecycle=None schema validation failed: {diagnostic}")
        if (directory / ".terraform.lock.hcl").read_bytes() != lock:
            raise RuntimeError("schema check changed the pinned lockfile")
    print(
        json.dumps(
            {
                "schema_check": "passed",
                "provider": "Azure/azapi@2.4.0",
                "resource_type": "Microsoft.App/jobs@2025-01-01",
                "boolean_lifecycle_rejected": True,
                "none_lifecycle_schema_valid": True,
                "azure_preflight": False,
                "backend_initialized": False,
                "live_job_support_proven": False,
                "runtime_acceptance": False,
                "activation_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
