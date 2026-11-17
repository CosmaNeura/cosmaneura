"""Uv package cache is opt-in and PR-Checks-only."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMMON_SETUP = ROOT / ".github/actions/common-setup/action.yml"
WORKFLOWS_DIR = ROOT / ".github/workflows"
DEPLOY_RELEASE_INFRA_PREFIXES = (
    "build_and_publish",
    "deploy_",
    "production_",
    "infra_",
)


def _common_setup() -> dict:
    return yaml.safe_load(COMMON_SETUP.read_text(encoding="utf-8"))


def test_uv_cache_is_opt_in_default_false_with_os_arch_exact_key() -> None:
    source = COMMON_SETUP.read_text(encoding="utf-8")
    action = _common_setup()
    assert action["inputs"]["enable_uv_cache"]["default"] == "false"
    assert "if: inputs.enable_uv_cache == 'true'" in source
    assert (
        "key: ${{ runner.os }}-${{ runner.arch }}-uv-${{ hashFiles('uv.lock') }}"
        in source
    )
    assert "restore-keys:" not in source.split("enable_uv_cache")[-1]
    assert "${{ runner.os }}-uv-" not in source
    # Shared enable_cache still gates node_modules / mise, not uv.
    assert "if: inputs.enable_cache == 'true'" in source


def test_only_pull_request_checks_enables_uv_cache() -> None:
    enabled_in: list[str] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "enable_uv_cache" not in text:
            continue
        workflow = yaml.safe_load(text)
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if step.get("uses") != "./.github/actions/common-setup":
                    continue
                with_block = step.get("with") or {}
                if str(with_block.get("enable_uv_cache", "false")).lower() == "true":
                    enabled_in.append(path.name)

    assert enabled_in, "expected Pull Request Checks to opt into uv cache"
    assert set(enabled_in) == {"pull_request_checks.yml"}

    # Frontend (web) is JS-only and must not pay for the uv package cache.
    pr = yaml.safe_load(
        (WORKFLOWS_DIR / "pull_request_checks.yml").read_text(encoding="utf-8")
    )
    web = pr["jobs"]["web"]
    web_setup = next(
        step
        for step in web["steps"]
        if step.get("uses") == "./.github/actions/common-setup"
    )
    assert str(
        (web_setup.get("with") or {}).get("enable_uv_cache", "false")
    ).lower() != ("true")


def test_deploy_release_and_infra_workflows_do_not_enable_uv_cache() -> None:
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        if not path.name.startswith(DEPLOY_RELEASE_INFRA_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8")
        assert 'enable_uv_cache: "true"' not in text
        assert "enable_uv_cache: true" not in text
        # Default remains false; explicit false is allowed but not required.
        if "uses: ./.github/actions/common-setup" not in text:
            continue
        workflow = yaml.safe_load(text)
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if step.get("uses") != "./.github/actions/common-setup":
                    continue
                with_block = step.get("with") or {}
                assert str(with_block.get("enable_uv_cache", "false")).lower() != "true"
