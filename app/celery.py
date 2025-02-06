"""

- Spawn is not natively supported and fork has been depreciated https://github.com/celery/celery/issues/6036#issuecomment-1151224775
"""

from celery import Celery, signals
from celery.app.task import Task
from kombu.utils.json import register_type
from typeid import TypeID

from app import log
from app.configuration.redis import redis_url
from app.configuration.sentry import configure_sentry


# TODO extact to activemodel
def register_typeid_encoder():
    "this ensures TypeID objects passed as arguments to a delayed function are properly serialized"

    def class_full_name(clz) -> str:
        return ".".join([clz.__module__, clz.__qualname__])

    def _encoder(obj: TypeID) -> str:
        return str(obj)

    def _decoder(data: str) -> TypeID:
        return TypeID.from_string(data)

    register_type(
        TypeID,
        class_full_name(TypeID),
        encoder=_encoder,
        decoder=_decoder,
    )


register_typeid_encoder()


class BaseTaskWithRetry(Task):
    autoretry_for = (Exception,)
    max_retries = 10
    retry_backoff = True
    retry_backoff_max = 700
    retry_jitter = True


celery = Celery(
    "tasks", broker=redis_url(), result_backend=redis_url(), task_cls=BaseTaskWithRetry
)


@signals.celeryd_init.connect
def init_sentry(**_kwargs):
    # according to sentry documentation, this must be done
    # https://docs.sentry.io/platforms/python/integrations/celery/
    configure_sentry()


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
    pass


# tag all jobs with the job name (module path) and uuid for the task
@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_kwargs):
    "https://github.com/hynek/structlog/issues/287#issuecomment-991182054"
    log.clear()
    log.local(task_id=task_id, task_name=task.name)


# TODO below should be cleaned up and is probably overkill


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

# celery.conf.beat_schedule = {
#     "example_normal": {
#         "task": "app.jobs.example_normal",
#         # can run sub-minute schedules
#         "schedule": timedelta(seconds=2),
#     },
#     "example_exactly_once": {
#         "task": "app.jobs.example_exactly_once",
#         "schedule": crontab(minute="*/60"),
#     },
#     "example_async": {
#         "task": "app.jobs.example_async",
#         "schedule": crontab(minute="*/5"),
#     },
# }

# define a hard timeout to limit infinitely running processes
celery.conf.task_time_limit = 60 * 35


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
