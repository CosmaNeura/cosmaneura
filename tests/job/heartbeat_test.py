from app.celery import celery_app
from app.jobs.heartbeat import perform


def test_heartbeat_perform_returns_none():
    """Heartbeat is intentionally a no-op; if it raises, Sentry would
    report task failures instead of the missed-beat signal we want."""
    assert perform() is None


def test_heartbeat_task_is_registered_under_expected_name():
    """beat_schedule references the task by string; if the registered
    name drifts, beat enqueues a task celery can't route and the cron
    monitor silently never fires."""
    assert "app.jobs.heartbeat.perform" in celery_app.tasks


def test_heartbeat_is_in_beat_schedule():
    """If the heartbeat entry is removed from beat_schedule, the Sentry
    cron monitor disappears too — and we lose the worker-alive signal
    this whole task exists to provide."""
    entry = celery_app.conf.beat_schedule.get("heartbeat")
    assert entry is not None
    assert entry["task"] == "app.jobs.heartbeat.perform"
