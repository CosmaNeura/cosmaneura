"""Default-off recovery sweep for provider-neutral desktop capture state.

The broker receives no tenant, patient, encounter, provider identifier, or
secret.  Each run scans durable receipts and the coordination service claims
and fences an individual provider lookup before any network call occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app import log
from app.celery import celery_app
from app.configuration.desktop_capture import (
    DesktopCaptureSettings,
    get_desktop_capture_settings,
)
from app.services.capture_coordination import (
    CaptureCoordinationConflict,
    CaptureCoordinationError,
    CaptureOutcomeUncertain,
    discover_due_segment_start_operations,
    mark_due_cleanup_blocked,
    reconcile_segment_start,
)
from app.services.capture_lifecycle import (
    CaptureLifecycleUnavailable,
    purge_expired_capture_secrets,
    reconcile_never_started_leases,
)
from app.services.capture_provider import CaptureProvider

RECONCILIATION_BATCH_SIZE = 4
CLEANUP_BATCH_SIZE = 50
LOCAL_SECRET_PURGE_BATCH_SIZE = 50
NEVER_STARTED_BATCH_SIZE = 25


@dataclass(frozen=True)
class CaptureMaintenanceResult:
    enabled: bool
    discovered: int
    recovered: int
    deferred: int
    review_required: int
    failed: int
    cleanup_newly_blocked: int
    cleanup_blocked_due: int
    capabilities_purged: int
    replay_responses_purged: int
    never_started_examined: int
    never_started_closed: int
    never_started_deferred: int
    local_maintenance_failed: int


def _default_provider(settings: DesktopCaptureSettings) -> CaptureProvider:
    """Build the lookup adapter only after the recovery gate finds due work."""

    from app.configuration.recall import RECALL_API_BASE, RECALL_API_KEY
    from app.services.recall_desktop import RecallDesktopAdapter

    return RecallDesktopAdapter(
        api_key=RECALL_API_KEY,
        api_base=RECALL_API_BASE,
        provider_create_enabled=settings.provider_create_enabled,
    )


def run_capture_maintenance(
    *,
    provider: CaptureProvider | None = None,
    settings: DesktopCaptureSettings | None = None,
    now: datetime | None = None,
    reconciliation_batch_size: int = RECONCILIATION_BATCH_SIZE,
    cleanup_batch_size: int = CLEANUP_BATCH_SIZE,
    local_secret_purge_batch_size: int = LOCAL_SECRET_PURGE_BATCH_SIZE,
    never_started_batch_size: int = NEVER_STARTED_BATCH_SIZE,
) -> CaptureMaintenanceResult:
    """Run one bounded reconciliation and cleanup-visibility pass."""

    capabilities_purged = 0
    replay_responses_purged = 0
    never_started_examined = 0
    never_started_closed = 0
    never_started_deferred = 0
    local_maintenance_failed = 0
    try:
        secret_purge = purge_expired_capture_secrets(
            now=now,
            limit=local_secret_purge_batch_size,
        )
        capabilities_purged = secret_purge.capabilities_purged
        replay_responses_purged = secret_purge.replay_responses_purged
    except CaptureLifecycleUnavailable:
        local_maintenance_failed += 1
        log.error(
            "capture local secret purge failed",
            event_code="capture_local_secret_purge_failed",
        )

    try:
        never_started = reconcile_never_started_leases(
            now=now,
            limit=never_started_batch_size,
        )
        never_started_examined = never_started.examined
        never_started_closed = never_started.closed
        never_started_deferred = never_started.deferred
        if never_started_deferred:
            log.warning(
                "capture never-started reconciliation requires review",
                event_code="capture_never_started_reconciliation_deferred",
                examined=never_started_examined,
                deferred=never_started_deferred,
            )
    except CaptureLifecycleUnavailable:
        local_maintenance_failed += 1
        log.error(
            "capture never-started reconciliation failed",
            event_code="capture_never_started_reconciliation_failed",
        )

    cleanup = mark_due_cleanup_blocked(now=now, limit=cleanup_batch_size)
    if cleanup.blocked_due:
        log.error(
            "capture cleanup requires operator action",
            event_code="capture_cleanup_blocked",
            newly_blocked=cleanup.newly_blocked,
            blocked_due=cleanup.blocked_due,
        )

    effective_settings = settings or get_desktop_capture_settings()
    # Recovery is independently gated so incident response can stop all new
    # capture creation without abandoning already-ambiguous provider outcomes.
    enabled = effective_settings.recovery_enabled
    if not enabled:
        log.info(
            "capture reconciliation disabled",
            event_code="capture_reconciliation_disabled",
        )
        return CaptureMaintenanceResult(
            enabled=False,
            discovered=0,
            recovered=0,
            deferred=0,
            review_required=0,
            failed=0,
            cleanup_newly_blocked=cleanup.newly_blocked,
            cleanup_blocked_due=cleanup.blocked_due,
            capabilities_purged=capabilities_purged,
            replay_responses_purged=replay_responses_purged,
            never_started_examined=never_started_examined,
            never_started_closed=never_started_closed,
            never_started_deferred=never_started_deferred,
            local_maintenance_failed=local_maintenance_failed,
        )

    operation_ids = discover_due_segment_start_operations(
        now=now,
        limit=reconciliation_batch_size,
    )
    active_provider = provider
    if operation_ids and active_provider is None:
        active_provider = _default_provider(effective_settings)

    recovered = 0
    deferred = 0
    review_required = 0
    failed = 0
    for operation_id in operation_ids:
        if active_provider is None:  # Defensive: non-empty work always builds one.
            failed += 1
            continue
        try:
            reconcile_segment_start(
                provider=active_provider,
                operation_id=operation_id,
                now=now,
            )
            recovered += 1
        except CaptureOutcomeUncertain:
            deferred += 1
        except CaptureCoordinationConflict:
            review_required += 1
        except CaptureCoordinationError:
            failed += 1
        except Exception:
            # The durable operation lease makes an interrupted/unexpected
            # attempt discoverable again.  Never serialize the exception: an
            # adapter exception may contain a URL, header, or response body.
            failed += 1

    if deferred or review_required or failed:
        log.warning(
            "capture reconciliation requires follow-up",
            event_code="capture_reconciliation_follow_up",
            discovered=len(operation_ids),
            recovered=recovered,
            deferred=deferred,
            review_required=review_required,
            failed=failed,
        )
    else:
        log.info(
            "capture reconciliation sweep completed",
            event_code="capture_reconciliation_completed",
            discovered=len(operation_ids),
            recovered=recovered,
        )

    return CaptureMaintenanceResult(
        enabled=True,
        discovered=len(operation_ids),
        recovered=recovered,
        deferred=deferred,
        review_required=review_required,
        failed=failed,
        cleanup_newly_blocked=cleanup.newly_blocked,
        cleanup_blocked_due=cleanup.blocked_due,
        capabilities_purged=capabilities_purged,
        replay_responses_purged=replay_responses_purged,
        never_started_examined=never_started_examined,
        never_started_closed=never_started_closed,
        never_started_deferred=never_started_deferred,
        local_maintenance_failed=local_maintenance_failed,
    )


def perform() -> None:
    """Celery entry point; all work is sourced from durable database rows."""

    run_capture_maintenance()


perform_celery = celery_app.task(
    name="app.jobs.capture_reconciliation.perform",
    autoretry_for=(),
    max_retries=0,
    time_limit=12 * 60,
)(perform)
