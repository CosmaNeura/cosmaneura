import hashlib
import logging
from importlib import import_module
from typing import Any

import celery_healthcheck
from celery import Celery, signals
from celery.app.task import Task
from celery.schedules import crontab
from celery.worker.request import Request as CeleryRequest

from app import log, root
from app.configuration.redis import redis_url
from app.configuration.sentry import configure_sentry

from activemodel.celery import register_celery_typeid_encoder

register_celery_typeid_encoder()

_REDACTED_TASK_VALUE = "<redacted>"
_FRAMEWORK_LOG_PREFIXES = ("celery", "kombu", "billiard", "flower")
_FRAMEWORK_LOG_RECORD_FACTORY_MARKER = (
    "__cosmaneura_metadata_only_framework_log_record_factory__"
)
_PAYLOAD_CAPABLE_LOGGERS = (
    *_FRAMEWORK_LOG_PREFIXES,
    "celery.app.trace",
    "celery.redirected",
    "celery.worker.request",
    "celery.worker.strategy",
    "celery.worker.consumer.consumer",
)
_SAFE_LOG_RECORD_FIELDS = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }
)
_PRIVATE_TASK_EVENT_FIELDS = frozenset(
    {
        "args",
        "argsrepr",
        "exception",
        "kwargs",
        "kwargsrepr",
        "reason",
        "result",
        "shadow",
        "traceback",
    }
)
_SAFE_TASK_INFO_FIELDS = frozenset(
    {
        "acknowledged",
        "hostname",
        "id",
        "name",
        "time_start",
        "type",
        "worker_pid",
    }
)


def _task_ref(task_id: object) -> str:
    digest = hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _is_framework_logger_name(name: object) -> bool:
    if not isinstance(name, str):
        return False
    return any(
        name == prefix or name.startswith(f"{prefix}.")
        for prefix in _FRAMEWORK_LOG_PREFIXES
    )


def _sanitize_framework_log_fields(fields: dict[str, Any]) -> None:
    if not _is_framework_logger_name(fields.get("name")):
        return

    for field in tuple(fields):
        if field not in _SAFE_LOG_RECORD_FIELDS:
            fields.pop(field, None)
    dict.__setitem__(fields, "msg", "task framework event")
    dict.__setitem__(fields, "args", ())
    dict.__setitem__(fields, "exc_info", None)
    dict.__setitem__(fields, "exc_text", None)
    dict.__setitem__(fields, "stack_info", None)


class _MetadataOnlyLogRecordDict(dict[str, Any]):
    """Reject fields that ``Logger.makeRecord`` adds after factory creation.

    Python applies a logging call's ``extra`` mapping after the global record
    factory returns. Restricting new keys here closes that otherwise-uncovered
    path while preserving updates to the standard fields already on a record.
    """

    def __setitem__(self, key: str, value: Any) -> None:
        if not _is_framework_logger_name(self.get("name")) or key in self:
            super().__setitem__(key, value)

    def update(self, *args: Any, **kwargs: Any) -> None:
        super().update(*args, **kwargs)
        _sanitize_framework_log_fields(self)


def _sanitize_framework_log_record(
    record: logging.LogRecord,
) -> logging.LogRecord:
    if not _is_framework_logger_name(record.name):
        # ``logging.makeLogRecord`` asks the factory for an empty template and
        # overlays the source record afterward (for example in structlog).
        if record.name is None and not isinstance(
            record.__dict__, _MetadataOnlyLogRecordDict
        ):
            record.__dict__ = _MetadataOnlyLogRecordDict(record.__dict__)
        return record

    _sanitize_framework_log_fields(record.__dict__)
    if not isinstance(record.__dict__, _MetadataOnlyLogRecordDict):
        record.__dict__ = _MetadataOnlyLogRecordDict(record.__dict__)
    return record


def _install_metadata_only_framework_record_factory() -> None:
    """Wrap the process factory once, retaining any previously installed one."""

    previous_factory = logging.getLogRecordFactory()
    if getattr(previous_factory, _FRAMEWORK_LOG_RECORD_FACTORY_MARKER, False):
        return

    def metadata_only_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return _sanitize_framework_log_record(previous_factory(*args, **kwargs))

    setattr(metadata_only_factory, _FRAMEWORK_LOG_RECORD_FACTORY_MARKER, True)
    logging.setLogRecordFactory(metadata_only_factory)


class MetadataOnlyFrameworkLogFilter(logging.Filter):
    """Reduce third-party task logs to fixed, non-payload metadata.

    Celery's own trace/request loggers serialize exception messages and
    tracebacks after our task signals run. Kombu can also include a malformed
    message body in decode errors. All application-owned task telemetry is
    emitted by the signals below, so framework records retain only their
    logger, severity, source location, timestamp, and a fixed event string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        _sanitize_framework_log_record(record)
        return True


def install_metadata_only_framework_logging() -> None:
    """Install process-wide sanitization plus handler-level defense in depth."""

    _install_metadata_only_framework_record_factory()

    for logger_name in _PAYLOAD_CAPABLE_LOGGERS:
        framework_logger = logging.getLogger(logger_name)
        if not any(
            isinstance(existing, MetadataOnlyFrameworkLogFilter)
            for existing in framework_logger.filters
        ):
            framework_logger.addFilter(MetadataOnlyFrameworkLogFilter())

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if not any(
            isinstance(existing, MetadataOnlyFrameworkLogFilter)
            for existing in handler.filters
        ):
            handler.addFilter(MetadataOnlyFrameworkLogFilter())


def _metadata_only_worker_configuration(_state, **_kwargs) -> dict[str, str]:
    return {"privacy_mode": "metadata_only"}


def _metadata_only_worker_report(_state, **_kwargs) -> dict[str, str]:
    return {"ok": "metadata_only"}


def install_metadata_only_worker_inspection() -> None:
    """Keep remote control but replace commands that disclose configuration.

    Flower needs ``ping``/``active``/``reserved`` and the application needs
    revoke. Celery's ``conf`` and ``report`` replies can contain extension
    settings such as the authenticated RedBeat URL because Celery's built-in
    key censor does not recognize every extension key.
    """

    panel = getattr(import_module("celery.worker.control"), "Panel")
    panel.data["conf"] = _metadata_only_worker_configuration
    panel.data["dump_conf"] = _metadata_only_worker_configuration
    panel.data["report"] = _metadata_only_worker_report


class MetadataOnlyRequest(CeleryRequest):
    """Prevent Flower/control replies and optional task events exposing data."""

    def info(self, safe: bool = False):
        del safe  # Callers cannot opt back into raw broker arguments.
        details = super().info(safe=True)
        for field in tuple(details):
            if field not in _SAFE_TASK_INFO_FIELDS:
                details.pop(field, None)
        details["args"] = _REDACTED_TASK_VALUE
        details["kwargs"] = _REDACTED_TASK_VALUE
        return details

    def send_event(self, type: str, **fields: Any) -> None:
        sanitized = dict(fields)
        for field in _PRIVATE_TASK_EVENT_FIELDS:
            if field in sanitized:
                sanitized[field] = _REDACTED_TASK_VALUE
        return super().send_event(type, **sanitized)


class BaseTaskWithRetry(Task):
    """
    Base task class with extended retry capabilities. The default retry sequence is very fast, we use a longer
    retry sequence in case an external system is down.

    This class extends the Celery Task class to implement a slower retry sequence,
    which is useful when dealing with external systems (like openai) that might be temporarily down
    or rate limited.

    Attributes:
        autoretry_for (tuple): Exceptions that trigger automatic retry. Default is (Exception,),
                              which means any exception will trigger a retry.
        max_retries (int): Maximum number of retry attempts. Default is 20.
        retry_backoff (bool): Whether to use exponential backoff. Default is True.
        retry_backoff_max (int): Maximum backoff time in seconds. Default is 700.
        retry_jitter (bool): Whether to add random jitter to backoff. Default is True.
    """

    autoretry_for = (Exception,)
    max_retries = 20
    retry_backoff = True
    retry_backoff_max = 700
    retry_jitter = True
    Request = MetadataOnlyRequest

    # Results, failures, tracebacks, and event payloads belong in the
    # application database/log contract, never Celery's Redis/Flower stores.
    ignore_result = True
    store_errors_even_if_ignored = False
    send_events = False

    def apply_async(
        self,
        args=None,
        kwargs=None,
        task_id=None,
        producer=None,
        link=None,
        link_error=None,
        shadow=None,
        *,
        countdown=None,
        eta=None,
        expires=None,
        retry=None,
        retry_policy=None,
        queue=None,
        exchange=None,
        routing_key=None,
        priority=None,
        serializer=None,
        compression=None,
        add_to_parent=None,
        publisher=None,
        headers=None,
        ignore_result=None,
        time_limit=None,
        soft_time_limit=None,
        **options,
    ):
        # Protocol v2 includes display-only argument representations in task
        # headers and task-sent/received events. The worker still receives the
        # real arguments in the broker body, but telemetry and inspection APIs
        # see only these fixed representations.
        named_options = {
            "countdown": countdown,
            "eta": eta,
            "expires": expires,
            "retry": retry,
            "retry_policy": retry_policy,
            "queue": queue,
            "exchange": exchange,
            "routing_key": routing_key,
            "priority": priority,
            "serializer": serializer,
            "compression": compression,
            "add_to_parent": add_to_parent,
            "publisher": publisher,
            "headers": headers,
            "time_limit": time_limit,
            "soft_time_limit": soft_time_limit,
        }
        options.update(
            {key: value for key, value in named_options.items() if value is not None}
        )
        options["argsrepr"] = _REDACTED_TASK_VALUE
        options["kwargsrepr"] = _REDACTED_TASK_VALUE
        options["ignore_result"] = True
        return super().apply_async(
            args=args,
            kwargs=kwargs,
            task_id=task_id,
            producer=producer,
            link=link,
            link_error=link_error,
            # A caller-provided shadow name is another telemetry field and may
            # not be derived from a provider or clinical identifier.
            shadow=self.name,
            **options,
        )

    # TODO should probably add a CM for the database session here...


celery_app = Celery(
    "tasks",
    broker=redis_url(),
    redbeat_redis_url=redis_url(),  # type: ignore
    task_cls=BaseTaskWithRetry,
)

celery_healthcheck.register(celery_app)

# Celery has no application consumer for task return values. Disable every
# result/event persistence surface explicitly; retries use broker messages and
# do not require a result backend.
celery_app.conf.result_backend = None
celery_app.conf.result_extended = False
celery_app.conf.result_persistent = False
celery_app.conf.accept_content = ["json"]
celery_app.conf.task_serializer = "json"
celery_app.conf.event_serializer = "json"
celery_app.conf.task_protocol = 2
celery_app.conf.task_ignore_result = True
celery_app.conf.task_store_errors_even_if_ignored = False
celery_app.conf.task_remote_tracebacks = False
celery_app.conf.task_send_sent_event = False
celery_app.conf.task_track_started = False
celery_app.conf.worker_send_task_events = False
celery_app.conf.worker_redirect_stdouts = True
celery_app.conf.worker_redirect_stdouts_level = "WARNING"

install_metadata_only_framework_logging()
install_metadata_only_worker_inspection()


@signals.before_task_publish.connect
def redact_task_protocol_metadata(headers=None, **_kwargs) -> None:
    """Cover direct ``send_task``/beat publishers as well as Task.apply_async."""

    if not isinstance(headers, dict):
        return
    headers["argsrepr"] = _REDACTED_TASK_VALUE
    headers["kwargsrepr"] = _REDACTED_TASK_VALUE
    headers["shadow"] = headers.get("task")


# define a hard timeout to limit infinitely running processes
# 35m is an arbitrary number, but should be long enough to handle most tasks
celery_app.conf.task_time_limit = 60 * 35

# By default, is a worker is killed without warning (OOM, etc) which can occur more easily in containerized environments
# the job will get into a stale state where it looks like it is executing indefinitely and will never be retried.
# in order to ensure that the job is retried if the process is killed abruptly, we need to enable two settings:
#
#   - acks_late: "The acks_late setting would be used when you need the task to be executed again if the worker (for some reason) crashes mid-execution"
#   - task_reject_on_worker_lost: job is not retried if the worker is lost
#
# References:
#
#   - https://stackoverflow.com/questions/45045980/what-different-between-task-reject-on-worker-lost-and-task-acks-late-in-celery
#   - https://stackoverflow.com/questions/69107860/celery-what-is-the-reason-to-have-acks-late-true-without-setting-task-reject-on
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.task_acks_late = True

celery_app.conf.beat_schedule_filename = str(root / "tmp/celerybeat-schedule")


@signals.celeryd_init.connect
def init_sentry(**_kwargs):
    # according to sentry documentation, this must be done here, even if it's executed on application start
    # https://docs.sentry.io/platforms/python/integrations/celery/
    from sentry_sdk.integrations.celery import CeleryIntegration

    configure_sentry(integrations=[CeleryIntegration(monitor_beat_tasks=True)])
    # Sentry initialization may add standard-library logging handlers after
    # module import; attach the framework scrubber to those handlers too.
    install_metadata_only_framework_logging()


@signals.setup_logging.connect
def receiver_setup_logging(
    loglevel, logfile, format, colorize, **kwargs
):  # pragma: no cover
    # this ensures celery does not override our application's logging configuration
    install_metadata_only_framework_logging()
    # A setup_logging receiver makes Celery treat logging as application-owned,
    # so it otherwise skips its normal stdout/stderr redirection. Route task
    # prints through the fixed-message framework filter explicitly.
    celery_app.log.redirect_stdouts(celery_app.conf.worker_redirect_stdouts_level)


@signals.worker_init.connect
def worker_init_import_jobs(sender, **kwargs):
    install_metadata_only_worker_inspection()
    import app.jobs  # noqa: F401
    # ensures all job classes are available to celery and avoids circular imports
    # that would be caused by adding them as a top-level import


@signals.worker_process_init.connect
def worker_process_init_setup_logging(**kwargs):
    # TODO should add PID and worker ID to the log content, we want the `WARNING/ForkPoolWorker-8` ID in the logs
    #      https://github.com/celery/celery/discussions/9378
    # might need to grab this from? https://pypi.org/project/setproctitle/#history
    # log.local(celery_pid=sender.pid)

    # Dispose AND reset the SQLAlchemy engine after fork to prevent inherited
    # file descriptors / libpq state from causing segfaults in psycopg.
    from activemodel.session_manager import SessionManager

    instance = SessionManager.get_instance()
    if instance._engine is not None:
        instance._engine.dispose()
        instance._engine = None

    # Same hazard, different pool: an inherited httpx connection would be shared
    # between sibling workers.
    from app.services.stripe_billing import reset_http_client

    reset_http_client()


# tag all jobs with the job name (module path) and uuid for the task
@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_kwargs):
    "https://github.com/hynek/structlog/issues/287#issuecomment-991182054"
    log.clear()
    task_reference = _task_ref(task_id)
    log.local(task_ref=task_reference, task_name=task.name)
    log.info(
        "task started",
        event_code="task_started",
        task_ref=task_reference,
        task_name=task.name,
    )


# TODO below should be cleaned up and is probably overkill


@signals.task_failure.connect
def on_task_failure(
    sender, task_id, exception, args, kwargs, traceback, einfo, **_kwargs
):
    log.error(
        "task failed",
        event_code="task_failed",
        task_name=sender.name,
        task_ref=_task_ref(task_id),
        error_type=type(exception).__name__,
    )


@signals.task_retry.connect
def on_task_retry(sender, request, reason, einfo, **_kwargs):
    log.warning(
        "task retrying",
        event_code="task_retrying",
        task_name=sender.name,
        task_ref=_task_ref(request.id),
        retries=request.retries,
        error_type=type(reason).__name__,
    )


@signals.task_postrun.connect
def on_task_postrun(sender, task_id, task, args, kwargs, retval, state, **_kwargs):
    log.info(
        "task finished",
        event_code="task_finished",
        task_ref=_task_ref(task_id),
        task_name=task.name,
        state=state,
    )
    log.clear()


@signals.worker_shutdown.connect
def capture_worker_stop(sender, **kwargs):
    log.clear()


# celery.conf.app_name = "tasks"
# celery.conf.log_level = "INFO"

# https://stackoverflow.com/questions/26831103/avoiding-duplicate-tasks-in-celery-broker
# celery.conf.ONCE = {
#     "backend": "celery_once.backends.Redis",
#     "settings": {"url": redis_url(), "default_timeout": 60 * 60},
# }

celery_app.conf.beat_schedule = {
    "visit_prep_sms_scheduler": {
        "task": "app.jobs.visit_prep_sms_scheduler.perform",
        "schedule": 60.0,
    },
    "visit_prep_sms_maintenance": {
        "task": "app.jobs.visit_prep_sms_maintenance.perform",
        "schedule": 60.0,
    },
    "visit_prep_email": {
        "task": "app.jobs.visit_prep_email.perform",
        "schedule": 15.0,
    },
    "visit_prep_email_maintenance": {
        "task": "app.jobs.visit_prep_email.maintain",
        "schedule": 60.0,
    },
    "visit_prep_synthesis": {
        "task": "app.jobs.visit_prep_synthesis.perform",
        "schedule": 5.0,
    },
    "visit_prep_synthesis_maintenance": {
        "task": "app.jobs.visit_prep_synthesis.maintain",
        "schedule": 60.0,
    },
    "session_prep_maintenance": {
        "task": "app.jobs.session_prep.maintain",
        "schedule": 60.0,
    },
    "session_prep": {
        "task": "app.jobs.session_prep.perform",
        "schedule": 5.0,
    },
    "sync_clerk": {
        "task": "app.jobs.sync_clerk.perform",
        "schedule": crontab(minute="0", hour="0"),
    },
    # Heartbeat proves the worker is alive end-to-end (beat → broker →
    # worker → task execution). Sentry's CeleryIntegration with
    # monitor_beat_tasks=True turns this into a cron monitor that pages
    # us if the heartbeat doesn't fire on schedule — the signal we
    # didn't have during the 2026-05-19 worker hang.
    "heartbeat": {
        "task": "app.jobs.heartbeat.perform",
        "schedule": 60.0,
    },
    # Synthetic check exercises the meeting.started handler against
    # production redis + db so config drift (rotated secrets, broken
    # DSNs, network policy changes) gets caught within 15 minutes
    # instead of when the next real user reports their bot didn't join.
    "synthetic_zoom_check": {
        "task": "app.jobs.synthetic_zoom_check.perform",
        "schedule": crontab(minute="*/15"),
    },
    # Daily sweep that finds AIVisitTranscripts older than 14 days
    # with no soft-delete marker and queues per-transcript
    # de-identification + tombstoning (issue #29). 03:00 UTC sits
    # outside US clinic hours.
    "transcript_cleanup": {
        "task": "app.jobs.transcript_cleanup.perform",
        "schedule": crontab(minute="0", hour="3"),
    },
    # Membership joins never fail because Stripe is temporarily unavailable.
    # Their desired paid quantity is persisted on the practice, and this sweep
    # repairs any drift until Stripe confirms it.
    "stripe_quantity_reconciliation": {
        "task": "app.jobs.stripe_billing.perform",
        "schedule": crontab(minute="*/15"),
    },
    # Webhooks return only after a signed event is committed to the DB inbox.
    # This sweep recovers broker publish failures and worker crashes quickly.
    "stripe_event_inbox_reconciliation": {
        "task": "app.jobs.stripe_events.perform",
        "schedule": crontab(minute="*"),
    },
    # Re-publishes committed webhook receipts after broker/process failures,
    # reclaims stale fenced leases, expires ciphertext, and resumes transcript
    # fan-out. The task itself performs no provider calls serially.
    "external_webhook_inbox_reconcile": {
        "task": "app.jobs.external_webhooks.reconcile",
        "schedule": 30.0,
    },
}

# Import crontab at the top of the file

# from app.configuration.database import clear_engine
# @task_prerun.connect
# def on_task_init(*args, **kwargs):
#     """
#     Without this, the database connection becomes corrupted and causes SSL-related failures:

#     https://github.com/celery/celery/issues/1564
#     https://github.com/celery/celery/issues/3238
#     https://stackoverflow.com/questions/45215596/flask-and-celery-on-heroku-sqlalchemy-exc-databaseerror-psycopg2-databaseerro
#     """

#     log.info("task init, clearing database engine")

#     clear_engine()
