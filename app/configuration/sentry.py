import sentry_sdk
import sentry_sdk.integrations
from decouple import config
from posthog.sentry.posthog_integration import PostHogIntegration

from ..environments import is_job_monitor, is_production, python_environment


def scrub_event(event, _hint=None):
    """
    PHI guard for error events. Log lines and exception messages in this
    codebase carry IDs only (by convention), but request payloads, cookies,
    and logging kwargs captured as breadcrumb data can contain clinical
    content — drop those wholesale rather than trying to pick PHI out of them.
    """
    request = event.get("request")
    if isinstance(request, dict):
        for field in ("data", "cookies", "headers", "query_string", "env", "url"):
            request.pop(field, None)

    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        for crumb in breadcrumbs.get("values", []):
            if isinstance(crumb, dict):
                crumb.pop("data", None)
                crumb.pop("message", None)

    exception = event.get("exception")
    if isinstance(exception, dict):
        values = exception.get("values")
        if isinstance(values, list):
            for value in values:
                if not isinstance(value, dict):
                    continue
                value.pop("value", None)
                stacktrace = value.get("stacktrace")
                if not isinstance(stacktrace, dict):
                    continue
                frames = stacktrace.get("frames")
                if isinstance(frames, list):
                    for frame in frames:
                        if isinstance(frame, dict):
                            frame.pop("vars", None)

    spans = event.get("spans")
    if isinstance(spans, list):
        for span in spans:
            if isinstance(span, dict):
                span.pop("data", None)

    for field in (
        "extra",
        "user",
        "tags",
        "contexts",
        "message",
        "logentry",
        "fingerprint",
    ):
        event.pop(field, None)

    return event


def configure_sentry(integrations=None):
    from app import log

    if not is_production():
        return

    if is_job_monitor():
        # we don't care about monitoring the job monitoring frontend
        return

    def filter_transactions(event, hint):
        """
        Filter out noisy urls that don't add any value to profiling

        - https://docs.sentry.io/platforms/python/configuration/filtering/
        - https://github.com/getsentry/sentry-docs/pull/6364/files
        """
        from urllib.parse import urlparse

        IGNORED_PATHS = ["/healthcheck", "/", "{path:path}"]

        request = event.get("request")
        url_string = request.get("url") if isinstance(request, dict) else None
        parsed_url = urlparse(url_string) if isinstance(url_string, str) else None

        if parsed_url is not None and (
            parsed_url.path in IGNORED_PATHS or parsed_url.path.startswith("/assets/")
        ):
            return None

        return scrub_event(event, hint)

    sentry_sdk.init(
        dsn=config("SENTRY_DSN", cast=str),
        release=config("BUILD_COMMIT", cast=str),
        environment=python_environment(),
        enable_tracing=True,
        traces_sample_rate=0.1,
        # posthog integration is not a standard integration included with Sentry
        # https://docs.sentry.io/platforms/python/integrations/
        integrations=[PostHogIntegration()] + (integrations or []),
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
