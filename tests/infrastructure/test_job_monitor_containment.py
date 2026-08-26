from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_job_monitor_is_absent_from_both_infrastructure_roots() -> None:
    for directory in ("infra", "infra-staging"):
        source = _read(f"{directory}/main.tf")
        active_lines = [
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        ]
        active_source = "\n".join(active_lines)

        assert 'resource "azurerm_container_app" "job-monitor"' not in active_source
        assert "FLOWER_PASSWORD" not in active_source
        assert "job_monitor_flower" not in active_source


def test_deployment_paths_cannot_recreate_or_update_job_monitor() -> None:
    for path in (
        "infra/deploy.sh",
        "infra-staging/deploy.sh",
        "infra/deploy_staging.sh",
    ):
        source = _read(path)
        assert "app-jmon" not in source
        assert "JMON_CONTAINER" not in source

    assert "job_monitor:" not in _read("Procfile")
    assert "FLOWER_PASSWORD" not in _read(".env.shared")
    assert '"flower>=' not in _read("pyproject.toml")
    assert 'name = "flower"' not in _read("uv.lock")


def test_production_state_and_access_helpers_no_longer_expect_job_monitor() -> None:
    assert "app-jmon" not in _read("infra/api_only_secret_live_guard.py")
    assert "app-jmon" not in _read("infra/database_url_live_guard.py")
    assert "app-jmon" not in _read("infra/database_url_cutover.py")
    assert "job-monitor" not in _read("infra/outputs.tf")
