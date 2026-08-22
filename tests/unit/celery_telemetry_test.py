import json
import logging
from importlib import import_module
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from celery.app.task import Task
from celery.backends.base import DisabledBackend
from celery.exceptions import Retry
from celery.worker.request import Request as CeleryRequest
from structlog.testing import capture_logs

from app import celery


def test_task_signals_hash_ids_and_omit_exception_payloads() -> None:
    raw_task_id = "raw-task-identifier-private"
    exception_sentinel = "provider-url-and-clinical-detail-sentinel"
    sender = SimpleNamespace(name="app.jobs.external_webhooks.process_delivery")
    request = SimpleNamespace(id=raw_task_id, retries=3)

    with capture_logs() as logs:
        celery.on_task_prerun(
            sender=sender,
            task_id=raw_task_id,
            task=sender,
            args=(exception_sentinel,),
            kwargs={"payload": exception_sentinel},
        )
        celery.on_task_failure(
            sender=sender,
            task_id=raw_task_id,
            exception=RuntimeError(exception_sentinel),
            args=(exception_sentinel,),
            kwargs={"payload": exception_sentinel},
            traceback=exception_sentinel,
            einfo=exception_sentinel,
        )
        celery.on_task_retry(
            sender=sender,
            request=request,
            reason=RuntimeError(exception_sentinel),
            einfo=exception_sentinel,
        )
        celery.on_task_postrun(
            sender=sender,
            task_id=raw_task_id,
            task=sender,
            args=(exception_sentinel,),
            kwargs={"payload": exception_sentinel},
            retval=exception_sentinel,
            state="FAILURE",
        )

    serialized = json.dumps(logs, sort_keys=True)
    assert raw_task_id not in serialized
    assert exception_sentinel not in serialized
    assert "sha256:" in serialized
    assert [entry["event_code"] for entry in logs] == [
        "task_started",
        "task_failed",
        "task_retrying",
        "task_finished",
    ]
    assert [entry["error_type"] for entry in logs if "error_type" in entry] == [
        "RuntimeError",
        "RuntimeError",
    ]
    assert logs[2]["retries"] == 3


def test_framework_log_filter_drops_payload_exception_and_custom_fields() -> None:
    sentinel = "raw-provider-exception-and-note-content"
    try:
        raise RuntimeError(sentinel)
    except RuntimeError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="celery.app.trace",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="task failed: %r",
        args=(RuntimeError(sentinel),),
        exc_info=exc_info,
    )
    record.data = {
        "args": (sentinel,),
        "kwargs": {"clinical": sentinel},
        "traceback": sentinel,
    }

    assert celery.MetadataOnlyFrameworkLogFilter().filter(record)

    serialized = json.dumps(record.__dict__, sort_keys=True, default=str)
    assert sentinel not in serialized
    assert record.getMessage() == "task framework event"
    assert record.exc_info is None
    assert not hasattr(record, "data")


def test_installed_filter_sanitizes_redirected_stdout_and_broker_logs() -> None:
    sentinel = "raw-transcript-printed-by-task"
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    root_logger = logging.getLogger()
    framework_loggers = [
        logging.getLogger("celery.redirected"),
        logging.getLogger("kombu.connection"),
    ]
    logger_states = [
        (logger, logger.disabled, logger.propagate) for logger in framework_loggers
    ]
    root_logger.addHandler(handler)

    try:
        for logger in framework_loggers:
            # Celery's pytest plugin may disable Kombu's pre-existing logger.
            # This test owns its emission state and restores it below.
            logger.disabled = False
            logger.propagate = True
        celery.install_metadata_only_framework_logging()
        framework_loggers[0].error("stdout: %s", sentinel)
        framework_loggers[1].error("broker rejected body: %s", sentinel)
    finally:
        root_logger.removeHandler(handler)
        for logger, disabled, propagate in logger_states:
            logger.disabled = disabled
            logger.propagate = propagate

    output = stream.getvalue()
    assert sentinel not in output
    assert output.splitlines() == ["task framework event", "task framework event"]


def test_record_factory_is_idempotent_and_preserves_previous_factory() -> None:
    original_factory = logging.getLogRecordFactory()
    previous_factory_calls = 0

    def previous_factory(*args, **kwargs) -> logging.LogRecord:
        nonlocal previous_factory_calls
        previous_factory_calls += 1
        record = original_factory(*args, **kwargs)
        record.created_by_previous_factory = True
        return record

    logging.setLogRecordFactory(previous_factory)
    try:
        celery.install_metadata_only_framework_logging()
        installed_factory = logging.getLogRecordFactory()
        celery.install_metadata_only_framework_logging()

        record = logging.getLogger("flower.monitor").makeRecord(
            name="flower.monitor",
            level=logging.ERROR,
            fn=__file__,
            lno=1,
            msg="monitor error: %s",
            args=("private-monitor-payload",),
            exc_info=None,
        )
        application_record = logging.getLogger("app.telemetry").makeRecord(
            name="app.telemetry",
            level=logging.INFO,
            fn=__file__,
            lno=1,
            msg="application event",
            args=(),
            exc_info=None,
        )
        reconstructed_record = logging.makeLogRecord(
            {
                "name": "billiard.pool",
                "msg": "worker crash: %s",
                "args": ("private-worker-payload",),
                "payload": "private-worker-payload",
            }
        )

        assert logging.getLogRecordFactory() is installed_factory
        assert previous_factory_calls == 3
        assert record.getMessage() == "task framework event"
        assert not hasattr(record, "created_by_previous_factory")
        assert getattr(application_record, "created_by_previous_factory", False) is True
        assert reconstructed_record.getMessage() == "task framework event"
        assert not hasattr(reconstructed_record, "payload")
    finally:
        logging.setLogRecordFactory(original_factory)


def test_record_factory_sanitizes_non_propagating_child_with_own_handler() -> None:
    sentinel = "raw-broker-body-on-private-child"
    captured_records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    logger = logging.getLogger("kombu.connection.private_transport")
    previous_handlers = logger.handlers.copy()
    previous_filters = logger.filters.copy()
    previous_level = logger.level
    previous_propagate = logger.propagate
    previous_disabled = logger.disabled
    logger.handlers = [CaptureHandler()]
    logger.filters = []
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    logger.disabled = False

    try:
        celery.install_metadata_only_framework_logging()
        logger.error(
            "broker rejected body: %s",
            sentinel,
            extra={"payload": {"clinical": sentinel}},
        )
    finally:
        logger.handlers = previous_handlers
        logger.filters = previous_filters
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        logger.disabled = previous_disabled

    assert len(captured_records) == 1
    record = captured_records[0]
    serialized = json.dumps(record.__dict__, sort_keys=True, default=str)
    assert sentinel not in serialized
    assert record.getMessage() == "task framework event"
    assert not hasattr(record, "payload")


def test_task_publish_metadata_uses_fixed_redacted_representations() -> None:
    sentinel = "raw-doctor-transcript-note-id"
    task = celery.BaseTaskWithRetry()
    task.name = "tests.metadata_only_task"
    task.bind(celery.celery_app)

    with patch.object(
        Task,
        "apply_async",
        autospec=True,
        return_value=SimpleNamespace(id="task-id"),
    ) as parent_apply_async:
        task.apply_async(
            args=(sentinel,),
            kwargs={"clinical": sentinel},
            argsrepr=sentinel,
            kwargsrepr=sentinel,
            shadow=sentinel,
            ignore_result=False,
        )

    published = parent_apply_async.call_args.kwargs
    assert published["argsrepr"] == "<redacted>"
    assert published["kwargsrepr"] == "<redacted>"
    assert published["shadow"] == task.name
    assert published["ignore_result"] is True

    protocol = getattr(celery.celery_app.amqp, "as_task_v2")(
        "task-id",
        "tests.metadata_only_task",
        args=(sentinel,),
        kwargs={"clinical": sentinel},
        argsrepr=published["argsrepr"],
        kwargsrepr=published["kwargsrepr"],
        create_sent_event=True,
    )
    exposed_metadata = json.dumps(
        {"headers": protocol.headers, "event": protocol.sent_event},
        sort_keys=True,
        default=str,
    )
    assert sentinel not in exposed_metadata
    assert exposed_metadata.count("<redacted>") >= 2

    direct_headers = {
        "task": "tests.direct_send_task",
        "argsrepr": sentinel,
        "kwargsrepr": sentinel,
        "shadow": sentinel,
    }
    celery.redact_task_protocol_metadata(headers=direct_headers)
    assert direct_headers == {
        "task": "tests.direct_send_task",
        "argsrepr": "<redacted>",
        "kwargsrepr": "<redacted>",
        "shadow": "tests.direct_send_task",
    }


def test_worker_inspection_and_optional_events_are_metadata_only() -> None:
    sentinel = "raw-task-payload-or-provider-exception"
    request = object.__new__(celery.MetadataOnlyRequest)

    with patch.object(
        CeleryRequest,
        "info",
        autospec=True,
        return_value={
            "id": "task-id",
            "args": sentinel,
            "kwargs": sentinel,
            "delivery_info": {"routing_key": sentinel},
            "custom_field": sentinel,
        },
    ) as parent_info:
        details = request.info()

    parent_info.assert_called_once_with(request, safe=True)
    assert details == {
        "id": "task-id",
        "args": "<redacted>",
        "kwargs": "<redacted>",
    }

    with patch.object(
        CeleryRequest,
        "send_event",
        autospec=True,
    ) as parent_send_event:
        request.send_event(
            "task-failed",
            exception=sentinel,
            traceback=sentinel,
            args=sentinel,
            argsrepr=sentinel,
            kwargs=sentinel,
            kwargsrepr=sentinel,
            result=sentinel,
            shadow=sentinel,
            runtime=1.25,
        )

    sent = parent_send_event.call_args.kwargs
    assert sent["runtime"] == 1.25
    for field in (
        "exception",
        "traceback",
        "args",
        "argsrepr",
        "kwargs",
        "kwargsrepr",
        "result",
        "shadow",
    ):
        assert sent[field] == "<redacted>"


def test_celery_has_no_result_or_task_event_persistence() -> None:
    assert isinstance(celery.celery_app.backend, DisabledBackend)
    assert celery.celery_app.conf.result_backend is None
    assert celery.celery_app.conf.result_extended is False
    assert celery.celery_app.conf.result_persistent is False
    assert celery.celery_app.conf.accept_content == ["json"]
    assert celery.celery_app.conf.task_serializer == "json"
    assert celery.celery_app.conf.event_serializer == "json"
    assert celery.celery_app.conf.task_protocol == 2
    assert celery.celery_app.conf.task_ignore_result is True
    assert celery.celery_app.conf.task_store_errors_even_if_ignored is False
    assert celery.celery_app.conf.task_remote_tracebacks is False
    assert celery.celery_app.conf.task_send_sent_event is False
    assert celery.celery_app.conf.worker_send_task_events is False
    assert celery.celery_app.conf.worker_redirect_stdouts is True
    assert celery.celery_app.conf.worker_redirect_stdouts_level == "WARNING"

    task = celery.BaseTaskWithRetry()
    assert task.ignore_result is True
    assert task.store_errors_even_if_ignored is False
    assert task.send_events is False
    assert task.Request is celery.MetadataOnlyRequest

    def metadata_only_task() -> None:
        return None

    registered = celery.celery_app.task(name="tests.metadata_only_defaults")(
        metadata_only_task
    )
    assert isinstance(registered, celery.BaseTaskWithRetry)
    assert registered.ignore_result is True
    assert registered.send_events is False
    assert registered.Request is celery.MetadataOnlyRequest


def test_metadata_hardening_preserves_automatic_retry() -> None:
    def retry_probe() -> None:
        raise RuntimeError("provider-response-sentinel")

    task = celery.celery_app.task(name="tests.metadata_only_retry_probe")(retry_probe)

    with pytest.raises(Retry) as captured:
        task.apply(throw=True)

    assert isinstance(captured.value.exc, RuntimeError)
    assert isinstance(task, celery.BaseTaskWithRetry)
    assert task.max_retries == 20
    assert task.retry_backoff is True


def test_custom_worker_logging_redirects_stdout_through_sanitized_logger() -> None:
    with patch.object(celery.celery_app.log, "redirect_stdouts") as redirect:
        celery.receiver_setup_logging(
            loglevel="INFO",
            logfile=None,
            format=None,
            colorize=False,
        )

    redirect.assert_called_once_with("WARNING")


def test_sensitive_worker_inspection_commands_return_fixed_metadata() -> None:
    panel = getattr(import_module("celery.worker.control"), "Panel")
    assert panel.data["conf"](None) == {"privacy_mode": "metadata_only"}
    assert panel.data["dump_conf"](None) == {"privacy_mode": "metadata_only"}
    assert panel.data["report"](None) == {"ok": "metadata_only"}
    assert "ping" in panel.data
    assert "revoke" in panel.data


def test_flower_is_non_persistent_and_has_no_payload_columns() -> None:
    procfile = (Path(__file__).resolve().parents[2] / "Procfile").read_text(
        encoding="utf-8"
    )
    job_monitor = next(
        line for line in procfile.splitlines() if line.startswith("job_monitor:")
    )

    assert "--enable_events=False" in job_monitor
    assert "--persistent=False" in job_monitor
    assert "--persistent=True" not in job_monitor
    assert "--db=" not in job_monitor
    assert "args" not in job_monitor.split("--tasks_columns=", 1)[1].split(",")
    assert "kwargs" not in job_monitor.split("--tasks_columns=", 1)[1].split(",")
    assert "result" not in job_monitor.split("--tasks_columns=", 1)[1].split(",")
