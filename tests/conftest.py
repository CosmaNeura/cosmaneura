import os
import sys

# When running locally, switching to the full-blown CI environment is a pain.
# To make it quick & easy to run tests, we force the environment to test and load cached CI environment variables (if we can).
# This will not be the same as CI, but it's closer and faster for devprod. It should never run on CI!
if os.environ["PYTHON_ENV"] != "test":
    print(
        "\033[91m"
        "PYTHON_ENV is not set to 'test', forcing.\n\n"
        "Additional variables may set in '.env.test' and required to run tests.\n\n"
        "Consider using `just py_test"
        "\033[0m"
        )

    os.environ["PYTHON_ENV"] = "test"

    assert 'app' not in sys.modules, "app modules should not be imported before environment is set"

    from tests.direnv import load_ci_environment
    load_ci_environment()

# important to ensure model metadata is added to the application
import app.models  # noqa: F401

import multiprocessing

from pathlib import Path

import pytest
from pytest import Config, FixtureRequest
from activemodel.pytest import database_reset_transaction, database_reset_truncate
from activemodel.session_manager import SessionManager
from decouple import config as decouple_config
from sqlalchemy import text

from app.configuration.database import run_migrations

from .constants import TEST_RESULTS_DIRECTORY
from .utils import delete_stale_clerk_test_users
from .seeds import seed_test_data
from .log import log


_APPEND_ONLY_TEST_TRIGGERS = {
    "ai_visit_note": "ai_visit_note_reject_delete",
    "ai_visit_note_version_audit": "ai_visit_note_version_audit_reject_mutation",
    "authority_audit_event": "authority_audit_event_append_only",
    "baa_acceptance": "baa_acceptance_reject_mutation",
    "practice_access_audit": "practice_access_audit_reject_mutation",
    "public_legal_acceptance": "public_legal_acceptance_reject_mutation",
    "credential_review_audit": "credential_review_audit_reject_mutation",
    "practice_invitation": "practice_invitation_guard_mutation",
    "superadmin_capability_audit": "superadmin_capability_audit_reject_mutation",
    "superadmin_capability_grant": "superadmin_capability_grant_guard_mutation",
    "view_as_audit_event": "view_as_audit_event_append_only",
    "view_as_session": "view_as_session_guard_mutation",
    "pilot_access_continuity_amendment": (
        "pilot_access_continuity_amendment_reject_mutation"
    ),
    "pilot_access_continuity_amendment_identity": (
        "pilot_access_continuity_amendment_identity_reject_mutation"
    ),
}


def database_reset_truncate_for_tests() -> None:
    """Reset fixtures without weakening production append-only enforcement.

    The application role cannot mutate legal/audit evidence. Test databases
    are owned by the test runner, so their cleanup temporarily disables only
    the named mutation triggers and always restores them. This keeps the
    real triggers enabled while every test executes, including the explicit
    UPDATE/DELETE rejection regressions.
    """

    assert os.environ["PYTHON_ENV"] == "test"
    engine = SessionManager.get_instance().get_engine()
    try:
        with engine.begin() as connection:
            for table_name, trigger_name in _APPEND_ONLY_TEST_TRIGGERS.items():
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}"
                    )
                )
        database_reset_truncate()
    finally:
        with engine.begin() as connection:
            for table_name, trigger_name in _APPEND_ONLY_TEST_TRIGGERS.items():
                connection.execute(
                    text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}")
                )

log.info("multiprocess start method", start_method=multiprocessing.get_start_method())

# NOTE this runs on any pytest invocation, even if no tests are run
def pytest_configure(config: Config):
    # TODO huh, maybe we should use anyio instead?
    # anyio is installed by some other packages and it's plugin is discovered automatically, we disable it in favor of asyncio
    config.option.plugins = ["no:anyio", "no:logging"]

    # when configuring in code, a tuple is used for the (mod, class) reference
    # check out _pytest/debugging.py for implementation details
    config.option.usepdb_cls = ('pdbr', 'RichPdb')

    config.option.disable_warnings = True

    # this forces pretty-traceback to be used instead of the default pytest tb, which is absolutely terrible
    config.option.tbstyle = "native"

    # playwright config
    config.option.screenshot = "only-on-failure"
    config.option.tracing = "retain-on-failure"
    # TODO although output is a generic CLI option, it's specific to playwright
    config.option.output = decouple_config("PLAYWRIGHT_RESULT_DIRECTORY", cast=str)

    # this forces pretty-traceback to be used instead of the default pytest tb, which is absolutely terrible
    config.option.tbstyle = "native"

    config.option.verbose = True

    # must be session to align with playwright expectations
    config.option.asyncio_mode = "auto"
    # TODO right now this option can only be set on ini, which is strange
    # config.option.asyncio_default_fixture_loop_scope = "session"

    # without this, if the test succeeds, no output is provided
    # this is a good default, but makes it much harder to debug what is going on
    config.option.log_cli = True
    # config.option.log_cli_level = "INFO"

    # lower debug level for file debugging, so we can download this artifact and view detailed debugging
    config.option.log_file = str(TEST_RESULTS_DIRECTORY / "pytest.log")
    # config.option.log_file_level = "DEBUG"

    config.option.enable_pretty_traceback = True
    config.option.enable_pretty_traceback_local_stack_only = decouple_config(
        "PRETTY_TRACEBACK_LOCAL_ONLY", default=False, cast=bool
    )

    config.option.playwright_visual_snapshots_path = decouple_config("PLAYWRIGHT_VISUAL_SNAPSHOT_DIRECTORY", cast=Path)
    config.option.playwright_visual_snapshot_failures_path = TEST_RESULTS_DIRECTORY / "playwright_visual_snapshot_failures"
    config.option.playwright_visual_snapshot_masks = [
        '[data-clerk-component="UserButton"]',
    ]

def pytest_sessionstart(session):
    "only executes once if a test is run, at the beginning of the test suite execution"

    # The Clerk development instance is shared with staging. Cleanup is limited
    # to the reserved namespace generated by our own ephemeral integration tests.
    delete_stale_clerk_test_users()
    # local test databases can drift when running pytest directly outside the full CI workflow
    run_migrations()
    # clear out any previous cruft in this DB, which is why...
    database_reset_truncate_for_tests()
    # we reseed the database with a base set of records
    seed_test_data()

# TODO we should really throw an exception if integration and non-integration tests are mixed, this will cause DB related issues
def is_integration_test(request: FixtureRequest):
    integration_tests = Path(__file__).parent / "integration"

    # should never occur!
    if not integration_tests.exists():
        raise FileNotFoundError(
            f"Integration tests directory does not exist: {integration_tests}"
        )

    current_test_path = request.node.path

    # is the current test within the integration tests directory?
    if str(current_test_path).startswith(str(integration_tests)):
        return True

    return False

# TODO we should probably wipe the redis DB on start as well?
@pytest.fixture(scope="function")
def sync_celery():
    "run celery tasks synchronously instead of in the background"

    # TODO I have to change the name of celery app... :/
    from app.celery import celery_app

    # celery settings to mutate
    settings = {
        'task_always_eager': True,
        'task_eager_propagates': True,
    }

    # Store original values
    original_settings = {key: celery_app.conf.get(key) for key in settings}

    # Set to sync mode
    celery_app.conf.update(settings)

    yield

    # Restore original values
    celery_app.conf.update(original_settings)


@pytest.fixture(scope="function", autouse=True)
def datatabase_reset_transaction_for_standard_tests(request):
    """
    Use a single entrypoint to determine what database reset strategy to use. It's important that transaction and truncation
    are not mixed, I saw very odd DB related errors when this happened.
    """

    if is_integration_test(request):
        # transaction truncation cannot be used with integration tests since the forked server does not retain the same
        # db connection in memory and therefore the transaction rollback does not work. To get around this, we truncate the
        # database before running any tests, although this also has the side effect of destroying any test seed data.
        database_reset_truncate_for_tests()

        # truncation also destroys the migration-seeded BAA version row, which
        # the onboarding clickwrap (accept_practice_baa) needs to exist
        from tests.routes.utils import ensure_current_baa_version

        ensure_current_baa_version()

        yield
        return

    yield from database_reset_transaction()
