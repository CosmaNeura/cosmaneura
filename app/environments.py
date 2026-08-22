import os
import typing as t

from decouple import config


def python_environment():
    return t.cast(str, config("PYTHON_ENV", default="development", cast=str)).lower()


def is_testing():
    return python_environment() == "test"


def is_local_testing():
    "for auto-building javascript assets when running tests locally"

    # TODO sys.platform != "darwin"?

    return is_testing() and config("CI", default=False, cast=bool) is False


def is_production():
    return python_environment() == "production"


def is_staging():
    return python_environment() == "staging"


def is_development():
    return python_environment() == "development"


def is_job_monitor():
    "is this the production flower application"
    # TODO should use a ENV var for app name, rather than hardcoding; how we determine & store container names needs to be refactored
    return config("CONTAINER_APP_NAME", default="", cast=str) == "cosmaneura-prod-jmon"


def is_pytest():
    """
    PYTEST_CURRENT_TEST is set by pytest to indicate the current test being run
    """
    return "PYTEST_CURRENT_TEST" in os.environ


def should_use_test_openai_fallback():
    """True when a job must use its offline stand-in instead of a live model.

    In the test environment this is unconditional. An automated test reaching a
    live model makes the result depend on a third party — a rate limit turns a
    green suite red — and bills real Azure credits on every CI run. Spending
    credits while developing is fine; spending them on every push is not.

    This deliberately does NOT check whether Azure credentials happen to be
    present. That was the previous behaviour, and because CI has them
    configured, the offline branch was never taken there: tests passed locally
    and failed in CI, and the live calls were invisible.

    Set ALLOW_LIVE_MODEL_IN_TESTS=true to opt a run into real model calls on
    purpose — useful when verifying prompt changes by hand.
    """

    if not is_testing():
        return False

    return not config("ALLOW_LIVE_MODEL_IN_TESTS", default=False, cast=bool)
