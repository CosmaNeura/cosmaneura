import json
from types import SimpleNamespace

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
    assert [entry["error_type"] for entry in logs] == [
        "RuntimeError",
        "RuntimeError",
    ]
    assert logs[1]["retries"] == 3
