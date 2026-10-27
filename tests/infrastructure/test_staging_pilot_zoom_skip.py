from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = "ENABLE_STAGING_PILOT_ZOOM_SKIP"
PUBLIC_FLAG = "VITE_STAGING_PILOT_ZOOM_SKIP"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_staging_pilot_zoom_skip_build_control_is_default_off() -> None:
    workflow = _read(".github/workflows/build_and_publish_local.yml")
    canonical = _read(".github/workflows/deploy_staging_candidate.yml")
    setting = f"{CONTROL}: ${{{{ vars.{CONTROL} || 'false' }}}}"
    # Only the canonical candidate workflow deploys staging, so only it may
    # carry this control.
    assert CONTROL not in workflow
    assert canonical.count(f"vars.{CONTROL}") > 0
    assert canonical.count(f"vars.{CONTROL}") == canonical.count(setting)
    assert f'"${CONTROL}" != "false"' in canonical

    staging = _read(".env.staging.frontend")
    assert f'export {PUBLIC_FLAG}="${{{CONTROL}:-false}}"' in staging

    direnv_recipe = _read("just/direnv.just")
    assert f'{CONTROL}="${{{CONTROL}:-false}}"' in direnv_recipe


def test_production_frontend_never_defines_staging_pilot_zoom_skip() -> None:
    production = _read(".env.production.frontend")
    assert CONTROL not in production
    assert PUBLIC_FLAG not in production
