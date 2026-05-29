import celery_healthcheck
from celery import Celery, signals
from celery.app.task import Task
from celery.schedules import crontab

from app import log, root
from app.configuration.redis import redis_url
from app.configuration.sentry import configure_sentry

from activemodel.celery import register_celery_typeid_encoder

register_celery_typeid_encoder()


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

    # TODO should probably add a CM for the database session here...


celery_app = Celery(
    "tasks",
    broker=redis_url(),
    result_backend=redis_url(),  # type: ignore
    redbeat_redis_url=redis_url(),  # type: ignore
    task_cls=BaseTaskWithRetry,
)

celery_healthcheck.register(celery_app)

# define a hard timeout to limit infinitely running processes
# 35m is an arbitrary number, but should be long enough to handle most tasks
celery_app.conf.task_time_limit = 60 * 35

# keep results around for 30d, otherwise they are pulled from the flower UI and it's hard to see what happened
celery_app.conf.result_expires = 60 * 60 * 24 * 30

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


@signals.setup_logging.connect
def receiver_setup_logging(
    loglevel, logfile, format, colorize, **kwargs
):  # pragma: no cover
    # this ensures celery does not override our application's logging configuration
    pass


@signals.worker_init.connect
def worker_init_import_jobs(sender, **kwargs):
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


# tag all jobs with the job name (module path) and uuid for the task
@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_kwargs):
    "https://github.com/hynek/structlog/issues/287#issuecomment-991182054"
    log.clear()
    log.local(task_id=task_id, task_name=task.name)


# TODO below should be cleaned up and is probably overkill


@signals.task_failure.connect
def on_task_failure(
    sender, task_id, exception, args, kwargs, traceback, einfo, **_kwargs
):
    import traceback as tb

    log.error(
        "task failed",
        task_name=sender.name,
        task_id=task_id,
        exception=repr(exception),
        traceback="".join(
            tb.format_exception(type(exception), exception, exception.__traceback__)
        ),
    )


@signals.task_retry.connect
def on_task_retry(sender, request, reason, einfo, **_kwargs):
    import traceback as tb

    exc = reason if isinstance(reason, BaseException) else None
    log.warning(
        "task retrying",
        task_name=sender.name,
        task_id=request.id,
        retries=request.retries,
        reason=repr(reason),
        traceback="".join(tb.format_exception(type(exc), exc, exc.__traceback__))
        if exc
        else None,
    )


@signals.task_postrun.connect
def on_task_postrun(sender, task_id, task, args, kwargs, retval, state, **_kwargs):
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

# TODO pulled from flower example, should we do this?
# app.conf.accept_content = ["pickle", "json", "msgpack", "yaml"]
# app.conf.worker_send_task_events = True

celery_app.conf.beat_schedule = {
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
