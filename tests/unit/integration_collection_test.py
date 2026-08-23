from __future__ import annotations

from pathlib import Path

from tests.integration.conftest import (
    _REPOSITORY_ROOT,
    _has_clerk_integration_prereqs,
    _requires_clerk_integration,
)


def test_clerk_integration_classifier_handles_pytest_absolute_paths() -> None:
    for name in (
        "onboarding_test.py",
        "settings_test.py",
        "upload_visit_test.py",
        "user_creation_test.py",
    ):
        path = _REPOSITORY_ROOT / "tests" / "integration" / name
        assert path.is_absolute()
        assert _requires_clerk_integration(path)

    assert not _requires_clerk_integration(
        _REPOSITORY_ROOT / "tests" / "integration" / "clerk_session_test.py"
    )
    assert not _requires_clerk_integration(Path("/outside/repository/settings_test.py"))


def test_public_placeholder_does_not_enable_live_clerk_tests(
    monkeypatch,
) -> None:
    monkeypatch.delenv("CLERK_FAPI_URL", raising=False)
    monkeypatch.delenv("CLERK_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("CLERK_TESTING_TOKEN", raising=False)
    monkeypatch.delenv("CLERK_PRIVATE_KEY", raising=False)
    monkeypatch.setenv(
        "VITE_CLERK_PUBLIC_KEY",
        "pk_test_Y2xlcmsucHVsbC1yZXF1ZXN0LmludmFsaWQk",
    )

    assert not _has_clerk_integration_prereqs()
