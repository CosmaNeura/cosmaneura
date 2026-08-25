import os
from pathlib import Path

import pytest

# server is imported to expose that fixture to the tests, but keep it all organized in the server file
from app import log

from tests.integration.clerk import parse_publishable_key
from tests.integration.javascript_build import start_js_build
from tests.integration.server import (  # noqa: F401
    report_localias_status,
    # IMPORTANT `server` is a fixture and this important exposes it to the tests
    server,
    terminate_server,
)

_LOCALIAS_READY = True
_CLERK_READY = True
_DATABASE_ONLY_FILES = {
    (Path(__file__).resolve().parent / "authority_closure_lock_test.py").resolve(),
    (Path(__file__).resolve().parent / "stripe_billing_state_race_test.py").resolve(),
    (
        Path(__file__).resolve().parent / "visit_prep_question_publish_lock_test.py"
    ).resolve(),
    (Path(__file__).resolve().parent / "visit_prep_commands_lock_test.py").resolve(),
}
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CLERK_REQUIRED_FILES = frozenset(
    {
        Path("tests/integration/onboarding_test.py"),
        Path("tests/integration/settings_test.py"),
        Path("tests/integration/upload_visit_test.py"),
        Path("tests/integration/user_creation_test.py"),
    }
)


def _database_only_run(config) -> bool:
    requested_paths = {
        Path(str(argument).split("::", 1)[0]).resolve() for argument in config.args
    }
    return bool(requested_paths) and requested_paths <= _DATABASE_ONLY_FILES


def _has_clerk_integration_prereqs() -> bool:
    frontend_api_url = os.environ.get("CLERK_FAPI_URL")
    if not frontend_api_url:
        publishable_key = os.environ.get("CLERK_PUBLISHABLE_KEY") or os.environ.get(
            "VITE_CLERK_PUBLIC_KEY"
        )
        if publishable_key and (
            parsed_publishable_key := parse_publishable_key(publishable_key)
        ):
            frontend_api_url = parsed_publishable_key.frontend_api

    has_token_or_private_key = bool(
        os.environ.get("CLERK_TESTING_TOKEN") or os.environ.get("CLERK_PRIVATE_KEY")
    )

    return bool(frontend_api_url and has_token_or_private_key)


def _requires_clerk_integration(path: Path) -> bool:
    try:
        repository_path = path.resolve().relative_to(_REPOSITORY_ROOT)
    except ValueError:
        return False
    return repository_path in _CLERK_REQUIRED_FILES


# NOTE this runs on any pytest invocation, even if no tests are run, once per pytest invocation
def pytest_configure(config):
    # start JS build right away, since it takes some time. Separately, the test server will wait until the build
    # is finished to start running tests against the server.
    global _LOCALIAS_READY, _CLERK_READY
    if _database_only_run(config):
        # These concurrency regressions need only Postgres. Keeping them free
        # of the JS/localias/Clerk harness ensures they actually run in a
        # focused CI or local invocation instead of becoming an infra skip.
        _LOCALIAS_READY = True
        _CLERK_READY = True
        return

    start_js_build()
    _LOCALIAS_READY = report_localias_status()
    _CLERK_READY = _has_clerk_integration_prereqs()


def pytest_collection_modifyitems(config, items):
    if not _LOCALIAS_READY:
        skip_marker = pytest.mark.skip(
            reason="integration tests require localalias daemon for https://*.cosma.localhost routing"
        )
        for item in items:
            if Path(str(item.fspath)).resolve() not in _DATABASE_ONLY_FILES:
                item.add_marker(skip_marker)
        return

    if _CLERK_READY:
        return

    clerk_skip_marker = pytest.mark.skip(
        reason=(
            "integration auth tests require Clerk testing prerequisites: "
            "CLERK_FAPI_URL or valid CLERK_PUBLISHABLE_KEY/VITE_CLERK_PUBLIC_KEY, "
            "plus CLERK_TESTING_TOKEN or CLERK_PRIVATE_KEY"
        )
    )
    for item in items:
        if _requires_clerk_integration(Path(str(item.fspath))):
            item.add_marker(clerk_skip_marker)


def pytest_keyboard_interrupt(excinfo):
    log.info("KeyboardInterrupt caught: stopping server...")
    terminate_server()

    # Force terminate all Playwright browser processes
    # try:
    #     # Method 1: Get playwright from pytest plugin (most reliable)
    #     from pytest_playwright.pytest_playwright import playwright_fixture

    #     if "playwright" in playwright_fixture._fixtures:
    #         log.info("Stopping Playwright instances...")
    #         playwright = playwright_fixture._fixtures["playwright"]
    #         playwright.stop()
    # except Exception as e:
    #     log.warning(f"Error stopping Playwright gracefully: {e}")

    # from _pytest.config import get_config

    # config = get_config()
    # instance = getattr(config, "my_playwright_instance", None)
    # if instance is not None:
    #     print("KeyboardInterrupt caught – stopping playwright...")
    #     instance.stop()
