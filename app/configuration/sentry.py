import sentry_sdk
import sentry_sdk.integrations
from decouple import config
from posthog.sentry.posthog_integration import PostHogIntegration

from ..environments import (
    is_job_monitor,
    is_production,
    is_staging,
    python_environment,
)


def scrub_event(event, _hint):
    """
    PHI guard for error events. Log lines and exception messages in this
    codebase carry IDs only (by convention), but request payloads, cookies,
    and logging kwargs captured as breadcrumb data can contain clinical
    content — drop those wholesale rather than trying to pick PHI out of them.
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            headers.pop("Authorization", None)
            headers.pop("Cookie", None)

    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        for crumb in breadcrumbs.get("values", []):
            if isinstance(crumb, dict):
                crumb.pop("data", None)

    return event


def configure_sentry(integrations=[]):
    from app import log

    # staging is included deliberately. It is the environment the promotion flow
    # depends on, and it was the only deployed environment running blind: every
    # other guard in app/configuration treats it like production
    # (database.py, debugging.py, static.py all check is_production() or
    # is_staging()). `environment=python_environment()` keeps the events
    # separated in Sentry.
    if not (is_production() or is_staging()):
        return

    if is_job_monitor():
        # we don't care about monitoring the job monitoring frontend.
        # NOTE this only ever matches production's jmon — the container name is
        # hardcoded. Staging no longer deploys one at all.
        return

    # setup() runs as an import side effect and is fail-fast, so an unset DSN
    # here would stop the container from booting rather than just disable error
    # reporting. Warn and carry on: losing Sentry should not lose the deploy.
    dsn = config("SENTRY_DSN", default="", cast=str)

    if not dsn:
        log.warning(
            "SENTRY_DSN is not set, skipping sentry configuration",
            environment=python_environment(),
        )
        return

    def filter_transactions(event, _hint):
        """
        Filter out noisy urls that don't add any value to profiling

        - https://docs.sentry.io/platforms/python/configuration/filtering/
        - https://github.com/getsentry/sentry-docs/pull/6364/files
        """
        from urllib.parse import urlparse

        IGNORED_PATHS = ["/healthcheck", "/", "{path:path}"]

        url_string = event["request"]["url"]
        parsed_url = urlparse(url_string)

        if parsed_url.path in IGNORED_PATHS or parsed_url.path.startswith("/assets/"):
            return None

        return event

    sentry_sdk.init(
        dsn=dsn,
        release=config("BUILD_COMMIT", cast=str),
        environment=python_environment(),
        enable_tracing=True,
        traces_sample_rate=0.1,
        # posthog integration is not a standard integration included with Sentry
        # https://docs.sentry.io/platforms/python/integrations/
        integrations=[PostHogIntegration()] + integrations,
        before_send=scrub_event,
        before_send_transaction=filter_transactions,
        # stack locals regularly hold transcripts/notes/prompts in this app
        include_local_variables=False,
        send_default_pii=False,
        _experiments={
            # Set continuous_profiling_auto_start to True
            # to automatically start the profiler on when
            # possible.
            "continuous_profiling_auto_start": True,
        },
    )

    log.info(
        "sentry configured",
        integrations=sentry_sdk.integrations._installed_integrations,
    )
