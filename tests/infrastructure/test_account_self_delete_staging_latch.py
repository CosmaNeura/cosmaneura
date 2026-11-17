"""Staging-only ON wiring for ACCOUNT_SELF_DELETE_ENABLED."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLAG = "ACCOUNT_SELF_DELETE_ENABLED"


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _shell_array(source: str, name: str) -> list[str]:
    start = source.index(f"\n{name}=(") + 1
    end = source.index("\n)", start)
    body = source[start:end]
    return [
        token
        for line in body.splitlines()
        for token in line.replace("(", " ").replace(")", " ").split()
        if token and not token.startswith("#")
    ]


def test_account_self_delete_is_on_in_staging_non_secret_config_only() -> None:
    staging = _read(".env.staging.backend")
    assert f"export {FLAG}=true" in staging

    production = ROOT / ".env.production.backend"
    if production.exists():
        assert FLAG not in production.read_text()

    shared = _read(".env.shared")
    assert FLAG not in shared


def test_account_self_delete_is_api_only_public_runtime() -> None:
    deploy = _read("infra-staging/deploy.sh")
    assert FLAG in _shell_array(deploy, "ACCOUNT_SELF_DELETE_API_ONLY_PUBLIC_VARS")
    assert FLAG not in _shell_array(deploy, "PUBLIC_VARS")
    assert (
        'container_public_vars+=("${ACCOUNT_SELF_DELETE_API_ONLY_PUBLIC_VARS[@]}")'
        in deploy
    )
