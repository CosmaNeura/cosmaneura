import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.configuration.server import API_BASE_URL
from app.configuration.zoom import ZOOM_WATCHDOG_ENABLED
from app.environments import is_production, is_staging
from app.jobs import merge_window, verify_bot_joined
from app.routes.recall_callbacks import zak_token_redis_key
from app.services import (
    alerting,
    recall,
    zoom_capture_authorization,
    zoom_webhook_operations,
)
from app.services import zoom as zoom_service
from app.services.doctor_access import doctor_access_in_force
from app.services.external_webhook_inbox import (
    PermanentWebhookError,
    RetryableWebhookError,
    complete_zoom_teardown_intent,
    digest_provider_reference,
    provider_log_ref,
    store_zoom_teardown_intent,
)
from app.services.recall import BOT_NAME

from activemodel.session_manager import get_session
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.patient import Patient
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from sqlmodel import select

# After the only allowed recording segment stops, keep the bot handle for this
# long so a confirmed leave can be retried without losing provider ownership.
# Recording resume is deliberately disabled until exact encounter-bound launch
# authorization exists.
MERGE_WINDOW_SECONDS = 600
# Recall fetches the ZAK immediately while joining the just-created bot. Keep
# its bearer URL valid only for that short join window; the callback consumes
# it atomically on first use.
_ZAK_URL_TOKEN_TTL_SECONDS = 5 * 60
# Serialize every Recall lifecycle transition for one Zoom meeting.  Recall
# calls have a 30-second request timeout and start_recording can also wait up
# to 45 seconds for the bot to become ready, so the lease is deliberately
# longer than the longest supported transition.  The merge generation below
# additionally fences revoked/stale Celery tasks after the lock is released.
_LIFECYCLE_LOCK_TIMEOUT_SECONDS = 180
_LIFECYCLE_LOCK_BLOCKING_TIMEOUT_SECONDS = 90

# Zoom bot dispatch is gated by an explicit per-environment allowlist. Staging
# and production are separate Zoom apps, but the same account can be connected
# in both; gating keeps the staging test account out of production (and vice
# versa) so one real meeting never gets two bots.
#   - staging:    only the dedicated reviewer and two synthetic lab accounts
#   - production: only the specific beta accounts below
#   - dev/test:   no gating (real bots aren't dispatched there anyway)
# The frontend mirrors these allowlists in web/app/utils/zoom-feature.ts.
# See _start_appointment_session.
STAGING_ZOOM_EMAILS = frozenset(
    {
        "zoom-reviewer@cosmaneura.com",
        "scribe-lab-a+clerk_test@cosmaneura.com",
        "scribe-lab-b+clerk_test@cosmaneura.com",
    }
)
PROD_ZOOM_EMAILS = frozenset(
    {
        "dev@cosmaneura.com",
        "greg@mycatholicdoctor.com",
        "kathleen@mycatholicdoctor.com",
        "john@cosmaneura.com",
        "ortegaoscar14@gmail.com",
        # Pilot cohort
        "sweeman@fiathealth.org",
        "christa.thornberry@mycatholicdoctor.com",
        "maura.buete@mycatholicdoctor.com",
        "sherin.george@mycatholicdoctor.com",
    }
)


SideEffectBoundary = Callable[[], None]


def zoom_bot_email_allowed(email: str | None) -> bool:
    normalized_email = (email or "").strip().lower()
    if is_staging():
        return normalized_email in STAGING_ZOOM_EMAILS
    if is_production():
        return normalized_email in PROD_ZOOM_EMAILS
    # Development/test never dispatches a real bot unless the provider clients
    # are explicitly substituted, so local integration tests stay ungated.
    return True


def _begin_side_effect(boundary: SideEffectBoundary | None) -> None:
    if boundary is not None:
        boundary()


def _doctor_for_zoom_host(host_id: Any) -> Doctor | None:
    return cast(
        Doctor | None,
        Doctor.where(
            Doctor.id
            == select(cast(Any, DoctorVideoIntegration.doctor_id))
            .where(
                (DoctorVideoIntegration.provider == VideoProvider.zoom)
                & (DoctorVideoIntegration.provider_user_id == host_id)
            )
            .scalar_subquery()
        ).first(),
    )


def handle_meeting_started(
    payload: dict[str, Any],
    before_side_effect: SideEffectBoundary | None = None,
):
    obj = payload.get("payload", {}).get("object", {})
    host_id = obj.get("host_id")
    meeting_id = obj.get("id")
    meeting_id_str = str(meeting_id)
    event_occurrence = _occurrence_digest(obj)

    log.info(
        "meeting.started received",
        host_ref=provider_log_ref(host_id),
        meeting_ref=provider_log_ref(
            str(meeting_id) if meeting_id is not None else None
        ),
    )

    redis = get_redis()
    # Retain the legacy 60-second duplicate fence for rolling compatibility,
    # but never use it as the identity of a meeting occurrence. A genuine
    # short meeting can start and end inside this window, and PMR meeting IDs
    # are reused indefinitely.
    lock_key = f"recall:meeting:{meeting_id}:starting_lock"
    _begin_side_effect(before_side_effect)
    acquired_legacy_fence = bool(redis.set(lock_key, "1", nx=True, ex=60))

    # Clear any orphaned state from a previous session with this meeting_id.
    # Personal meeting rooms reuse the same ID, so meeting.ended may not have
    # fired (or may have been missed) before the next meeting.started arrives.
    # Preserve the lock we just acquired — it gates duplicate meeting.started
    # for the rest of this session.
    #
    # This runs immediately after the lock and *before* the doctor lookups
    # below, which cost ~200ms of DB round-trips. participant_joined for the
    # host lands ~50ms after meeting.started and is handled by a separate,
    # concurrent celery task: clearing late used to wipe the doctor_present
    # and participants_order that the concurrent handler had already written,
    # leaving the session stuck in "holding" with no bot.
    with _lifecycle_lock(redis, meeting_id_str):
        active_occurrence = _rget(
            redis, f"recall:meeting:{meeting_id}:occurrence_digest"
        )
        tracked_bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
        if event_occurrence is None and active_occurrence is not None:
            log.warning(
                "uuid-less meeting start fenced from occurrence-bound state",
                meeting_ref=provider_log_ref(meeting_id_str),
            )
            return
        if (
            active_occurrence is not None
            and event_occurrence is not None
            and active_occurrence != event_occurrence
        ):
            # Order cannot be inferred from arrival time. A delayed start from
            # an earlier PMR occurrence must never replace the currently bound
            # doctor/participant state. Only canonical meeting.ended clears an
            # occurrence and permits the next one to bind.
            log.warning(
                "meeting.started fenced from active different occurrence",
                meeting_ref=provider_log_ref(meeting_id_str),
            )
            return
        duplicate_active_occurrence = bool(
            event_occurrence is not None
            and active_occurrence == event_occurrence
            and _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")
        )
        if duplicate_active_occurrence:
            log.info(
                "duplicate meeting.started for active occurrence, rechecking authorization",
                meeting_ref=provider_log_ref(meeting_id_str),
            )
        if (
            not duplicate_active_occurrence
            and event_occurrence is None
            and not acquired_legacy_fence
        ):
            log.info(
                "legacy meeting.started duplicate within lock window, skipping",
                meeting_ref=provider_log_ref(meeting_id_str),
            )
            return
        if not duplicate_active_occurrence and tracked_bot_id:
            # A duplicate meeting.started (or a reused PMR whose prior bot did
            # not confirm leave) is not authority to discard provider
            # ownership. Keep every key intact so meeting.ended, expiry, or an
            # operator can finish teardown safely.
            log.warning(
                "meeting start blocked while prior bot ownership is unresolved",
                meeting_ref=provider_log_ref(str(meeting_id)),
                bot_ref=provider_log_ref(tracked_bot_id),
            )
            return
        continuing_race = not duplicate_active_occurrence and (
            event_occurrence is not None
            and active_occurrence == event_occurrence
            and not _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")
        )
        if not duplicate_active_occurrence and not continuing_race:
            _clear_meeting_keys(redis, meeting_id_str, preserve_lock=True)
            if event_occurrence is not None:
                redis.set(
                    f"recall:meeting:{meeting_id}:occurrence_digest",
                    event_occurrence,
                    ex=timedelta(hours=24),
                )
            log.info(
                "cleared stale meeting keys",
                meeting_ref=provider_log_ref(meeting_id_str),
            )

    # Database lookups do not mutate lifecycle state and intentionally run
    # outside the Redis lock. A participant delivery may arrive here; it binds
    # to the occurrence above, then the second lock below verifies the same
    # occurrence is still active before publishing the server mapping.
    doctor = _doctor_for_zoom_host(host_id)
    if not doctor or not doctor_access_in_force(doctor):
        log.info(
            "zoom session registration skipped: access unavailable",
            host_ref=provider_log_ref(host_id),
        )
        return

    integration = zoom_service.get_zoom_integration(doctor)
    if not integration:
        log.info(
            "doctor has no zoom integration, skipping session registration",
            doctor_ref=provider_log_ref(str(doctor.id)),
        )
        return

    with _lifecycle_lock(redis, meeting_id_str):
        if (
            _rget(redis, f"recall:meeting:{meeting_id}:occurrence_digest")
            != event_occurrence
        ):
            log.warning(
                "meeting start mapping fenced after occurrence changed",
                meeting_ref=provider_log_ref(meeting_id_str),
            )
            return
        ttl = timedelta(hours=24)
        redis.set(f"recall:meeting:{meeting_id}:doctor_id", str(doctor.id), ex=ttl)
        redis.set(f"recall:meeting:{meeting_id}:host_id", host_id, ex=ttl)
        occurrence_armed = (
            event_occurrence is not None
            and zoom_capture_authorization.arm_authorized_occurrence_locked(
                redis=redis,
                doctor_id=doctor.id,
                zoom_meeting_id=meeting_id_str,
                occurrence_digest=event_occurrence,
            )
        )

    log.info(
        "meeting session started, awaiting doctor",
        meeting_ref=provider_log_ref(meeting_id_str),
        doctor_ref=provider_log_ref(str(doctor.id)),
    )

    zoom_mtg = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=meeting_id_str)

    if zoom_mtg and zoom_mtg.status == ZoomMeetingStatus.scheduled:
        zoom_mtg.status = ZoomMeetingStatus.started
        zoom_mtg.save()

    patient_count = _rget(redis, f"recall:meeting:{meeting_id}:patient_count")
    if (
        occurrence_armed
        and zoom_mtg is not None
        and redis.get(f"recall:meeting:{meeting_id}:doctor_present")
        and patient_count is not None
        and patient_count.isdigit()
        and int(patient_count) > 0
    ):
        from app.jobs import start_zoom_capture as start_zoom_capture_job

        start_zoom_capture_job.queue(str(zoom_mtg.id))


def arm_join_watchdog(
    meeting_id: str, occurrence_digest: str | None, meeting_record_id: str
) -> None:
    """Persist monitoring; let a durable caller retry persistence failure."""
    if not ZOOM_WATCHDOG_ENABLED or occurrence_digest is None:
        return
    try:
        verify_bot_joined.queue_durably(
            meeting_record_id, meeting_id, occurrence_digest
        )
    except Exception as exc:
        log.warning(
            "join watchdog could not be armed",
            meeting_ref=provider_log_ref(meeting_id),
            error_type=type(exc).__name__,
        )
        try:
            alerting.alert(
                alerting.WatchdogArmingFailed,
                meeting_ref=provider_log_ref(meeting_id),
                error_type=type(exc).__name__,
            )
        except Exception:
            # Alerting about a broken dependency must not raise either.
            log.exception("failed to report watchdog arming failure")
        raise


def _arm_started_watchdog_after_success(payload: dict[str, Any]) -> None:
    """Retry-safe observer hook after the core Zoom operation succeeds."""
    if not ZOOM_WATCHDOG_ENABLED:
        return
    obj = payload.get("payload", {}).get("object", {})
    meeting_id_value = obj.get("id")
    host_id = obj.get("host_id")
    occurrence = _occurrence_digest(obj)
    if meeting_id_value is None or not host_id or occurrence is None:
        return
    meeting_id = str(meeting_id_value)
    doctor = _doctor_for_zoom_host(host_id)
    if doctor is None:
        return
    meeting = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=meeting_id)
    if meeting is None or not verify_bot_joined._capture_still_expected(
        str(meeting.id), meeting_id, occurrence
    ):
        return
    redis = get_redis()
    active_occurrence = _rget(redis, f"recall:meeting:{meeting_id}:occurrence_digest")
    active_doctor_id = _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")
    if (active_occurrence is not None and active_occurrence != occurrence) or (
        active_doctor_id is not None and active_doctor_id != str(doctor.id)
    ):
        return
    arm_join_watchdog(meeting_id, occurrence, str(meeting.id))


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def _rlindex(redis, key: str, idx: int) -> str | None:
    val = redis.lindex(key, idx)
    return val.decode() if isinstance(val, bytes) else cast(str | None, val)


def _rhget(redis, key: str, field: str) -> str | None:
    val = redis.hget(key, field)
    return val.decode() if isinstance(val, bytes) else cast(str | None, val)


def lifecycle_lock_key(meeting_id: str) -> str:
    """The lock every mutation of a meeting's session state is taken under.

    Public because jobs.verify_bot_joined takes the same lock before deciding a
    session has failed -- with its own, much shorter blocking timeout, since a
    watchdog must never hold a worker slot waiting on a live handler. Sharing
    the key rather than the constructor keeps the two from drifting apart.
    """
    return f"recall:meeting:{meeting_id}:lifecycle_lock"


def _lifecycle_lock(redis, meeting_id: str):
    return redis.lock(
        lifecycle_lock_key(meeting_id),
        timeout=_LIFECYCLE_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=_LIFECYCLE_LOCK_BLOCKING_TIMEOUT_SECONDS,
    )


def _occurrence_digest(obj: dict[str, Any]) -> str | None:
    """Return a non-reversible identity for one Zoom meeting occurrence."""

    occurrence_uuid = obj.get("uuid")
    if not isinstance(occurrence_uuid, str) or not occurrence_uuid.strip():
        return None
    return digest_provider_reference(f"zoom-occurrence:{occurrence_uuid}")


def _participant_ref(
    meeting_id: str,
    occurrence_digest: str | None,
    participant_id: str,
) -> str:
    """Return a non-reversible occurrence-scoped participant membership key."""

    return digest_provider_reference(
        f"zoom-participant:{meeting_id}:{occurrence_digest or 'legacy'}:{participant_id}"
    )


def _participant_event_timestamp(payload: dict[str, Any]) -> int | None:
    value = payload.get("event_ts")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _apply_participant_event_order(
    redis,
    *,
    meeting_id: str,
    participant_ref: str,
    event_timestamp: int | None,
    joined: bool,
) -> bool:
    """Apply one signed participant event only when it is newer than prior state."""

    prefix = f"recall:meeting:{meeting_id}"
    order_key = f"{prefix}:participant_event_order"
    # Zoom's current signed contract supplies event_ts. UUID-less payloads from
    # an old worker may not, so they retain only duplicate suppression during a
    # quiescent rolling cutover and must never replace timestamped state.
    if event_timestamp is None:
        marker = "legacy:1" if joined else "legacy:2"
        previous = _rhget(redis, order_key, participant_ref)
        if previous == marker or (
            previous is not None and not previous.startswith("legacy:")
        ):
            return False
    else:
        marker = f"{event_timestamp}:{1 if joined else 2}"
        previous = _rhget(redis, order_key, participant_ref)
        if previous is not None:
            if previous.startswith("legacy:"):
                previous_order = (-1, int(previous.rsplit(":", 1)[1]))
            else:
                previous_timestamp, previous_kind = previous.split(":", 1)
                previous_order = (int(previous_timestamp), int(previous_kind))
            current_order = (event_timestamp, 1 if joined else 2)
            if current_order <= previous_order:
                return False

    ttl_seconds = int(timedelta(hours=24).total_seconds())
    redis.hset(order_key, participant_ref, marker)
    redis.expire(order_key, ttl_seconds)
    return True


def _set_participant_membership(
    redis,
    *,
    meeting_id: str,
    participant_ref: str,
    joined: bool,
) -> int:
    """Update an idempotent participant set and its derived compatibility count."""

    prefix = f"recall:meeting:{meeting_id}"
    members_key = f"{prefix}:participants_present"
    order_key = f"{prefix}:participants_order"
    ttl_seconds = int(timedelta(hours=24).total_seconds())
    if joined:
        if redis.sadd(members_key, participant_ref):
            redis.rpush(order_key, participant_ref)
    else:
        redis.srem(members_key, participant_ref)
        redis.lrem(order_key, 0, participant_ref)

    count = cast(int, redis.scard(members_key))
    redis.set(f"{prefix}:patient_count", count, ex=ttl_seconds)
    redis.expire(members_key, ttl_seconds)
    redis.expire(order_key, ttl_seconds)
    return count


def _bind_participant_occurrence(
    redis,
    *,
    meeting_id: str,
    event_occurrence: str | None,
) -> bool:
    """Fence a participant event to the active PMR occurrence.

    During a rolling cutover, a session created by an old worker has no
    occurrence key.  The first UUID-bearing event may adopt that legacy state
    only when the authoritative server mapping already exists. Participant
    deliveries never establish a new occurrence by themselves. Once a UUID is
    bound, UUID-less and mismatched events always fail closed.
    """

    prefix = f"recall:meeting:{meeting_id}"
    active_occurrence = _rget(redis, f"{prefix}:occurrence_digest")
    if active_occurrence is not None:
        if event_occurrence == active_occurrence:
            return True
        log.warning(
            "zoom participant event fenced from a different occurrence",
            meeting_ref=provider_log_ref(meeting_id),
            event_has_occurrence=event_occurrence is not None,
        )
        return False

    if event_occurrence is None:
        # Rolling compatibility for state established entirely by old workers.
        return True

    if not _rget(redis, f"{prefix}:doctor_id"):
        return False

    redis.set(
        f"{prefix}:occurrence_digest",
        event_occurrence,
        ex=timedelta(hours=24),
    )
    log.info(
        "adopted zoom occurrence for rolling lifecycle state",
        meeting_ref=provider_log_ref(meeting_id),
    )
    return True


def _role_for(participant: dict, host_id: str | None) -> str:
    if participant.get("is_bot") is True or participant.get("user_name") == BOT_NAME:
        return "bot"
    if host_id and participant.get("id") == host_id:
        return "doctor"
    return "other"


def _resolve_host_id(redis, meeting_id: str, participant: dict) -> str | None:
    """The meeting's Zoom host_id, falling back to the database.

    meeting.started and participant_joined arrive as two independent celery
    tasks and run concurrently on the prefork worker. meeting.started needs
    ~200ms of DB round-trips before it writes host_id to Redis, but Zoom
    fires participant_joined for the host only ~50ms later — so the handler
    for the doctor's own join can read this key before it exists, classify
    the doctor as "other" and park the session in "non-doctor joined before
    doctor, holding". No bot is ever dispatched and the visit is lost.

    When the key is missing we ask the same source of truth meeting.started
    uses: a zoom DoctorVideoIntegration whose provider_user_id is this
    participant. That makes classification independent of task ordering, and
    also covers a meeting.started webhook that was dropped entirely.
    """
    host_id = _rget(redis, f"recall:meeting:{meeting_id}:host_id")
    if host_id:
        return host_id

    # Zoom leaves participant.id empty for guests, and a guest is never the
    # host — so an empty candidate means "not the doctor", not "look it up".
    candidate = participant.get("id")
    if not candidate:
        return None

    integration = cast(
        DoctorVideoIntegration | None,
        DoctorVideoIntegration.where(
            (DoctorVideoIntegration.provider == VideoProvider.zoom)
            & (DoctorVideoIntegration.provider_user_id == candidate)
        ).first(),
    )
    if not integration:
        return None

    log.info(
        "host_id absent from redis, resolved zoom host from db",
        meeting_ref=provider_log_ref(str(meeting_id)),
        participant_ref=provider_log_ref(candidate),
    )
    return candidate


def _start_appointment_session(
    meeting_id: str,
    _participant_id: str,
    _user_name: str,
):
    """Start one recording for an already server-bound appointment."""
    redis = get_redis()
    if redis.get(f"recall:meeting:{meeting_id}:recording_active"):
        return

    doctor_id = _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")
    if not doctor_id:
        log.warning(
            "cannot start appointment: no doctor_id in redis",
            meeting_ref=provider_log_ref(str(meeting_id)),
        )
        return

    doctor = cast(Doctor | None, Doctor.get(id=doctor_id))
    if not doctor or not doctor_access_in_force(doctor):
        log.warning(
            "cannot start appointment: access unavailable",
            doctor_ref=provider_log_ref(doctor_id),
            meeting_ref=provider_log_ref(str(meeting_id)),
        )
        return

    if not zoom_bot_email_allowed(doctor.email):
        log.info(
            "zoom bot dispatch blocked (email not in environment allowlist)",
            doctor_ref=provider_log_ref(doctor_id),
            meeting_ref=provider_log_ref(str(meeting_id)),
        )
        return

    try:
        meeting_url = zoom_service.get_meeting_join_url(doctor, meeting_id)
    except zoom_service.ZoomIntegrationInvalid:
        log.warning(
            "cannot start appointment: doctor revoked Zoom OAuth",
            doctor_ref=provider_log_ref(doctor_id),
            meeting_ref=provider_log_ref(str(meeting_id)),
        )
        return

    appointment = _consume_bound_appointment(
        doctor_id,
        str(meeting_id),
        occurrence_digest=_rget(
            redis, f"recall:meeting:{meeting_id}:occurrence_digest"
        ),
    )
    if appointment is None:
        # Never create a bot or start PHI capture until the server has bound
        # this Zoom meeting to one exact, tenant-valid appointment.
        return

    # Recall fetches the ZAK just-in-time from our /external/v1/recall/zak/{token}
    # endpoint when the bot joins. The token in the URL is the only auth Recall
    # sends, so it must be unguessable and short-lived; we map it to the doctor
    # in Redis with the same TTL as the ZAK itself.
    url_token = secrets.token_urlsafe(32)
    redis.set(zak_token_redis_key(url_token), doctor_id, ex=_ZAK_URL_TOKEN_TTL_SECONDS)
    zak_url = f"{API_BASE_URL}/external/v1/recall/zak/{url_token}"

    bot_data = recall.create_bot(
        meeting_url=meeting_url,
        zak_url=zak_url,
        manual_recording=True,
    )
    bot_id = bot_data["id"]
    ttl = timedelta(hours=24)
    redis.set(f"recall:meeting:{meeting_id}:bot_id", bot_id, ex=ttl)
    # Until recording-scoped ownership is fully established, a worker death
    # or ambiguous start response must cause this bot to be released rather
    # than leave an untracked recorder in the call.
    launch_teardown_intent = store_zoom_teardown_intent(
        action="release",
        meeting_id=str(meeting_id),
        bot_id=bot_id,
        occurrence_digest=_rget(
            redis, f"recall:meeting:{meeting_id}:occurrence_digest"
        ),
        generation=None,
        stop_recording=True,
        cleanup_session=False,
    )

    recording_data = recall.start_recording(bot_id)
    recording_id = recording_data.get("id")
    if not recording_id:
        log.warning(
            "bot never reached ready state, aborting appointment",
            bot_ref=provider_log_ref(bot_id),
            meeting_ref=provider_log_ref(str(meeting_id)),
        )
        recall.leave_call(bot_id)
        redis.delete(f"recall:meeting:{meeting_id}:bot_id")
        complete_zoom_teardown_intent(launch_teardown_intent.delivery_id)
        return

    # Until Phase 2 provides a durable, exact encounter authorization, one
    # Zoom meeting lifecycle may own only one recording segment.  Persist the
    # fence as soon as Recall confirms the first recording ID; it intentionally
    # outlives merge-window cleanup and is reset only by a new meeting.started
    # (or the canonical meeting.ended cleanup).
    redis.set(f"recall:meeting:{meeting_id}:capture_started", "1", ex=ttl)
    redis.set(f"recall:meeting:{meeting_id}:recording_active", "1", ex=ttl)

    # Reinforce the recording disclosure with an in-call chat notice. Best-effort
    # only: Zoom's native recording-consent prompt is the primary gate, so a
    # failure here must never abort a recording that already started.
    try:
        recall.send_chat_message(bot_id, recall.CONSENT_CHAT_MESSAGE)
    except Exception as exc:
        log.warning(
            "failed to post recording disclosure to zoom chat",
            bot_ref=provider_log_ref(bot_id),
            meeting_ref=provider_log_ref(str(meeting_id)),
            error_type=type(exc).__name__,
        )

    redis.set(f"recall:recording:{recording_id}:doctor_id", doctor_id, ex=ttl)
    redis.set(
        f"recall:recording:{recording_id}:appointment_id",
        str(appointment.id),
        ex=ttl,
    )
    redis.set(
        f"recall:recording:{recording_id}:patient_id",
        str(appointment.patient_id),
        ex=ttl,
    )
    complete_zoom_teardown_intent(launch_teardown_intent.delivery_id)
    log.info(
        "appointment session started",
        meeting_ref=provider_log_ref(str(meeting_id)),
        bot_ref=provider_log_ref(bot_id),
        appointment_ref=provider_log_ref(str(appointment.id)),
    )

    zoom_mtg = ZoomMeeting.get(doctor_id=doctor_id, zoom_meeting_id=str(meeting_id))
    if zoom_mtg:
        zoom_mtg.status = ZoomMeetingStatus.started
        zoom_mtg.save()


def handle_participant_joined(
    payload: dict[str, Any],
    before_side_effect: SideEffectBoundary | None = None,
):
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")
    participant = obj.get("participant", {})
    # Zoom only populates `participant.id` for signed-in account users; for
    # guests it arrives empty. We fall back to `user_id`, the meeting-scoped
    # identifier Zoom always sets, so the participants queue can index every
    # join — otherwise guest joins push empty strings and the doctor-arrives
    # branch never finds a first_id, so _start_appointment_session never runs.
    participant_id = participant.get("id") or participant.get("user_id") or ""

    meeting_id_str = str(meeting_id)
    event_occurrence = _occurrence_digest(obj)
    event_timestamp = _participant_event_timestamp(payload)
    redis = get_redis()
    host_id = _resolve_host_id(redis, meeting_id_str, participant)
    role = _role_for(participant, host_id)

    if role == "bot":
        log.info(
            "participant joined: bot",
            role="bot",
            meeting_ref=provider_log_ref(str(meeting_id)),
            participant_ref=provider_log_ref(participant_id),
        )
        return

    if not participant_id:
        if before_side_effect is not None:
            raise PermanentWebhookError()
        log.warning(
            "zoom participant join ignored without stable participant identity",
            meeting_ref=provider_log_ref(meeting_id_str),
        )
        return
    if event_occurrence is not None and event_timestamp is None:
        if before_side_effect is not None:
            raise PermanentWebhookError()
        log.warning(
            "zoom participant join ignored without signed event ordering",
            meeting_ref=provider_log_ref(meeting_id_str),
        )
        return

    with _lifecycle_lock(redis, meeting_id_str):
        occurrence_bound = _bind_participant_occurrence(
            redis,
            meeting_id=meeting_id_str,
            event_occurrence=event_occurrence,
        )
        # A durable participant delivery can arrive before meeting.started. It
        # must retry until that authoritative event establishes the occurrence
        # and doctor mapping; participant arrival order is never authority.
        if not occurrence_bound:
            active_occurrence = _rget(
                redis, f"recall:meeting:{meeting_id}:occurrence_digest"
            )
            if before_side_effect is not None and active_occurrence is None:
                raise RetryableWebhookError("meeting_mapping_unavailable")
            return
        if before_side_effect is not None and not _rget(
            redis, f"recall:meeting:{meeting_id}:doctor_id"
        ):
            raise RetryableWebhookError("meeting_mapping_unavailable")

        participant_reference = _participant_ref(
            meeting_id_str, event_occurrence, participant_id
        )
        _begin_side_effect(before_side_effect)
        if not _apply_participant_event_order(
            redis,
            meeting_id=meeting_id_str,
            participant_ref=participant_reference,
            event_timestamp=event_timestamp,
            joined=True,
        ):
            log.info(
                "stale or duplicate participant join ignored",
                meeting_ref=provider_log_ref(meeting_id_str),
                participant_ref=provider_log_ref(participant_id),
            )
            return

        if role == "doctor":
            ttl = timedelta(hours=24)
            redis.set(f"recall:meeting:{meeting_id}:doctor_present", "1", ex=ttl)

            log.info(
                "participant joined: doctor",
                role="doctor",
                meeting_ref=provider_log_ref(meeting_id_str),
                participant_ref=provider_log_ref(participant_id),
                doctor_present=True,
            )

            if redis.get(f"recall:meeting:{meeting_id}:recording_active"):
                return

            first_id = _rlindex(
                redis,
                f"recall:meeting:{meeting_id}:participants_order",
                0,
            )
            if not first_id:
                return

            _resume_or_start_recording_locked(
                redis,
                meeting_id=meeting_id_str,
                participant_id=first_id,
                user_name="",
            )
            return

        new_count = _set_participant_membership(
            redis,
            meeting_id=meeting_id_str,
            participant_ref=participant_reference,
            joined=True,
        )

        doctor_present = bool(redis.get(f"recall:meeting:{meeting_id}:doctor_present"))
        recording_active = bool(
            redis.get(f"recall:meeting:{meeting_id}:recording_active")
        )

        log.info(
            "participant joined: other",
            role="other",
            meeting_ref=provider_log_ref(meeting_id_str),
            participant_ref=provider_log_ref(participant_id),
            doctor_present=doctor_present,
            recording_active=recording_active,
            patient_count=new_count,
        )

        if not doctor_present:
            log.info(
                "non-doctor joined before doctor, holding",
                meeting_ref=provider_log_ref(meeting_id_str),
            )
            return

        if not recording_active:
            _resume_or_start_recording_locked(
                redis, meeting_id_str, participant_reference, ""
            )
        else:
            log.info(
                "additional participant in active session",
                meeting_ref=provider_log_ref(meeting_id_str),
                patient_count=new_count,
            )


def _revoke_merge_window_task(task_id: str | None, meeting_id: str) -> None:
    if not task_id:
        return
    try:
        celery_app.control.revoke(task_id)
    except Exception as exc:
        # recording_active plus the monotonically increasing generation are
        # the correctness fences. Revocation only saves a stale task run, so
        # a broker/control-plane error must not undo confirmed local teardown.
        log.warning(
            "merge window task revoke failed after lifecycle transition",
            meeting_ref=provider_log_ref(str(meeting_id)),
            error_type=type(exc).__name__,
        )
    else:
        log.info(
            "merge window task revoked",
            meeting_ref=provider_log_ref(str(meeting_id)),
        )


def _resume_or_start_recording(
    meeting_id: str,
    participant_id: str,
    user_name: str,
) -> None:
    """Start the first recording segment for a Zoom meeting lifecycle.

    A paused bot proves that a prior segment exists, but it does not prove the
    returning participant belongs to the same patient or encounter.  Until an
    exact encounter authorization exists, keep the bot handle available for
    confirmed teardown and reject every resume/second-segment attempt.
    """
    redis = get_redis()
    with _lifecycle_lock(redis, meeting_id):
        _resume_or_start_recording_locked(redis, meeting_id, participant_id, user_name)


def _resume_or_start_recording_locked(
    redis,
    meeting_id: str,
    participant_id: str,
    user_name: str,
) -> None:
    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
    capture_started = bool(redis.get(f"recall:meeting:{meeting_id}:capture_started"))
    if bot_id or capture_started:
        # A reconnect after a legitimate pause also reaches this boundary.
        # Preserve the prior capture for teardown and refuse every second
        # segment, without paging as if a new capture had been authorized.
        log.warning(
            "recording continuation blocked without exact encounter binding",
            meeting_ref=provider_log_ref(str(meeting_id)),
            bot_ref=provider_log_ref(bot_id) if bot_id else None,
            capture_started=capture_started,
        )
        return

    # The caller holds the lifecycle lock so two join deliveries cannot both
    # observe an empty slot and create bots.
    _start_appointment_session(meeting_id, participant_id, user_name)


def _consume_bound_appointment(
    doctor_id: str,
    meeting_id: str,
    *,
    occurrence_digest: str | None,
) -> Appointment | None:
    """Consume and return an exact server-bound appointment or fail closed.

    Zoom participant IDs and display names are not chart identifiers.  The
    legacy path may use only a ZoomMeeting appointment binding plus explicit
    occurrence-scoped authorization created by a trusted server-side workflow.
    The authorization is consumed under a database row lock before any bot
    joins, while appointment_id remains available for dashboard navigation.
    """
    now = datetime.now(UTC)
    with get_session() as session:
        zoom_mtg = session.exec(
            select(ZoomMeeting)
            .where(
                ZoomMeeting.doctor_id == doctor_id,
                ZoomMeeting.zoom_meeting_id == meeting_id,
            )
            .with_for_update()
        ).one_or_none()
        if not zoom_mtg or not zoom_mtg.appointment_id:
            log.warning(
                "zoom capture blocked: exact appointment binding unavailable",
                doctor_ref=provider_log_ref(doctor_id),
                meeting_ref=provider_log_ref(meeting_id),
            )
            return None

        expires_at = zoom_mtg.capture_authorization_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if (
            occurrence_digest is None
            or zoom_mtg.capture_authorization_occurrence_digest != occurrence_digest
            or expires_at is None
            or expires_at <= now
            or zoom_mtg.capture_authorization_consumed_at is not None
        ):
            log.warning(
                "zoom capture blocked: occurrence authorization unavailable",
                doctor_ref=provider_log_ref(doctor_id),
                meeting_ref=provider_log_ref(meeting_id),
            )
            return None

        appointment = session.get(Appointment, zoom_mtg.appointment_id)
        patient = (
            session.get(Patient, appointment.patient_id)
            if appointment is not None
            else None
        )
        if (
            appointment is None
            or str(appointment.doctor_id) != doctor_id
            or patient is None
            or str(patient.doctor_id) != doctor_id
        ):
            log.warning(
                "zoom capture blocked: invalid appointment tenant binding",
                doctor_ref=provider_log_ref(doctor_id),
                meeting_ref=provider_log_ref(meeting_id),
            )
            return None

        existing_transcript = session.exec(
            select(AIVisitTranscript)
            .where(AIVisitTranscript.appointment_id == appointment.id)
            .limit(1)
        ).first()
        if existing_transcript is not None:
            log.warning(
                "zoom capture blocked: appointment already has a transcript",
                doctor_ref=provider_log_ref(doctor_id),
                meeting_ref=provider_log_ref(meeting_id),
                appointment_ref=provider_log_ref(str(appointment.id)),
            )
            return None

        # Consume before the external mutation. If bot creation fails, the
        # trusted producer must deliberately re-authorize this occurrence.
        zoom_mtg.capture_authorization_consumed_at = now
        session.add(zoom_mtg)
        session.commit()
        session.refresh(appointment)
        session.expunge(appointment)
        return appointment


def handle_participant_left(
    payload: dict[str, Any],
    before_side_effect: SideEffectBoundary | None = None,
):
    """Decrement the patient counter. Stop recording and destroy the bot
    when the last non-doctor leaves while the doctor is still present —
    this closes the current appointment and lets the next non-doctor
    arrival open a fresh appointment/recording.

    Doctor leaving schedules a grace-period stop task (Task 8).
    """
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")
    participant = obj.get("participant", {})
    # Zoom only populates `participant.id` for signed-in account users; for
    # guests it arrives empty. We fall back to `user_id`, the meeting-scoped
    # identifier Zoom always sets, so the participants queue can index every
    # join — otherwise guest joins push empty strings and the doctor-arrives
    # branch never finds a first_id, so _start_appointment_session never runs.
    participant_id = participant.get("id") or participant.get("user_id") or ""

    meeting_id_str = str(meeting_id)
    event_occurrence = _occurrence_digest(obj)
    event_timestamp = _participant_event_timestamp(payload)
    redis = get_redis()
    host_id = _resolve_host_id(redis, meeting_id_str, participant)
    role = _role_for(participant, host_id)

    if role == "bot":
        log.info(
            "participant left: bot",
            role="bot",
            meeting_ref=provider_log_ref(str(meeting_id)),
            participant_ref=provider_log_ref(participant_id),
        )
        return

    if not participant_id:
        if before_side_effect is not None:
            raise PermanentWebhookError()
        log.warning(
            "zoom participant leave ignored without stable participant identity",
            meeting_ref=provider_log_ref(meeting_id_str),
        )
        return
    if event_occurrence is not None and event_timestamp is None:
        if before_side_effect is not None:
            raise PermanentWebhookError()
        log.warning(
            "zoom participant leave ignored without signed event ordering",
            meeting_ref=provider_log_ref(meeting_id_str),
        )
        return

    with _lifecycle_lock(redis, meeting_id_str):
        occurrence_bound = _bind_participant_occurrence(
            redis,
            meeting_id=meeting_id_str,
            event_occurrence=event_occurrence,
        )
        if not occurrence_bound:
            active_occurrence = _rget(
                redis, f"recall:meeting:{meeting_id}:occurrence_digest"
            )
            if before_side_effect is not None and active_occurrence is None:
                raise RetryableWebhookError("meeting_mapping_unavailable")
            return
        if before_side_effect is not None and not _rget(
            redis, f"recall:meeting:{meeting_id}:doctor_id"
        ):
            raise RetryableWebhookError("meeting_mapping_unavailable")

        participant_reference = _participant_ref(
            meeting_id_str, event_occurrence, participant_id
        )
        _begin_side_effect(before_side_effect)
        if not _apply_participant_event_order(
            redis,
            meeting_id=meeting_id_str,
            participant_ref=participant_reference,
            event_timestamp=event_timestamp,
            joined=False,
        ):
            log.info(
                "stale or duplicate participant leave ignored",
                meeting_ref=provider_log_ref(meeting_id_str),
                participant_ref=provider_log_ref(participant_id),
            )
            return

        if role == "doctor":
            redis.delete(f"recall:meeting:{meeting_id}:doctor_present")
            recording_active = bool(
                redis.get(f"recall:meeting:{meeting_id}:recording_active")
            )
            log.info(
                "participant left: doctor",
                role="doctor",
                meeting_ref=provider_log_ref(meeting_id_str),
                participant_ref=provider_log_ref(participant_id),
                doctor_present=False,
                recording_active=recording_active,
            )

            if recording_active:
                _pause_and_arm_merge_window_locked(
                    redis, meeting_id_str, reason="doctor_left"
                )
            return

        new_count = _set_participant_membership(
            redis,
            meeting_id=meeting_id_str,
            participant_ref=participant_reference,
            joined=False,
        )

        doctor_present = bool(redis.get(f"recall:meeting:{meeting_id}:doctor_present"))
        recording_active = bool(
            redis.get(f"recall:meeting:{meeting_id}:recording_active")
        )

        log.info(
            "participant left: other",
            role="other",
            meeting_ref=provider_log_ref(meeting_id_str),
            participant_ref=provider_log_ref(participant_id),
            doctor_present=doctor_present,
            recording_active=recording_active,
            patient_count=new_count,
        )

        if new_count == 0 and doctor_present and recording_active:
            _pause_and_arm_merge_window_locked(
                redis,
                meeting_id_str,
                reason="last_patient_left",
            )


def _pause_and_arm_merge_window(redis, meeting_id: str, reason: str) -> None:
    """Pause the active recording: stop the bot (it stays connected) and
    arm the merge_window task. Returns never resume this bot: the task owns
    confirmed leave and only then clears the provider handle."""
    with _lifecycle_lock(redis, meeting_id):
        _pause_and_arm_merge_window_locked(redis, meeting_id, reason)


def _pause_and_arm_merge_window_locked(redis, meeting_id: str, reason: str) -> None:
    # Another participant event may have completed the same pause while this
    # handler waited for the lock. Re-check inside the serialized lifecycle
    # boundary before issuing any provider mutation.
    if not redis.get(f"recall:meeting:{meeting_id}:recording_active"):
        return

    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
    if not bot_id:
        raise RuntimeError("active recording has no tracked bot")

    previous_generation = _rget(redis, f"recall:meeting:{meeting_id}:merge_generation")
    generation = int(previous_generation or "0") + 1
    occurrence_digest = _rget(redis, f"recall:meeting:{meeting_id}:occurrence_digest")
    intent = store_zoom_teardown_intent(
        action="pause",
        meeting_id=meeting_id,
        bot_id=bot_id,
        occurrence_digest=occurrence_digest,
        generation=generation,
        stop_recording=True,
        cleanup_session=False,
    )
    release_intent = store_zoom_teardown_intent(
        action="release",
        meeting_id=meeting_id,
        bot_id=bot_id,
        occurrence_digest=occurrence_digest,
        generation=generation,
        stop_recording=True,
        cleanup_session=False,
        recovery_delay=timedelta(seconds=MERGE_WINDOW_SECONDS),
    )

    try:
        recall.stop_recording(bot_id)
    except Exception as exc:
        log.warning(
            "stop_recording failed during pause",
            bot_ref=provider_log_ref(bot_id),
            meeting_ref=provider_log_ref(str(meeting_id)),
            reason=reason,
            error_type=type(exc).__name__,
        )
        # The encrypted inbox intent retains the bot handle and target state;
        # reconciliation can safely retry even if Redis or this worker is lost.
        raise

    redis.set(f"recall:meeting:{meeting_id}:merge_generation", generation)
    redis.delete(f"recall:meeting:{meeting_id}:recording_active")

    ttl = timedelta(hours=24)
    redis.expire(
        f"recall:meeting:{meeting_id}:merge_generation",
        int(ttl.total_seconds()),
    )
    redis.set(
        f"recall:meeting:{meeting_id}:paused_at",
        datetime.utcnow().isoformat(),
        ex=ttl,
    )
    task = merge_window.queue(
        meeting_id,
        generation,
        occurrence_digest=occurrence_digest,
        release_delivery_id=release_intent.delivery_id,
        countdown=MERGE_WINDOW_SECONDS,
    )
    redis.set(
        f"recall:meeting:{meeting_id}:merge_task_id",
        task.id,
        ex=ttl,
    )
    redis.set(
        f"recall:meeting:{meeting_id}:merge_release_intent_id",
        release_intent.delivery_id,
        ex=ttl,
    )
    complete_zoom_teardown_intent(intent.delivery_id)
    log.info(
        "recording paused, merge window armed",
        meeting_ref=provider_log_ref(str(meeting_id)),
        bot_ref=provider_log_ref(bot_id),
        reason=reason,
        merge_generation=generation,
        countdown_s=MERGE_WINDOW_SECONDS,
    )


def _clear_meeting_keys(redis, meeting_id: str, preserve_lock: bool = False) -> None:
    """Delete session state while retaining the cross-session generation fence.

    Called on meeting.ended so operational keys don't linger for the full 24h
    TTL — especially important for personal meeting rooms that reuse the same
    meeting_id across sessions.

    preserve_lock keeps the starting_lock in place when we are mid-session
    (e.g. cleaning orphan state during handle_meeting_started) so duplicate
    meeting.started webhooks for the same session still get deduped.
    """
    occurrence = _rget(redis, f"recall:meeting:{meeting_id}:occurrence_digest")
    suffixes = [
        "bot_id",
        "doctor_id",
        "host_id",
        "doctor_present",
        "recording_active",
        "participants_order",
        "participants_names",
        "participants_present",
        "participant_event_order",
        "patient_count",
        "capture_started",
        "occurrence_digest",
        "merge_task_id",
        "merge_release_intent_id",
        "paused_at",
        "watchdog_queued",
        "watchdog_alerted",
    ]
    if occurrence is not None:
        suffixes.append(f"watchdog_alerted:{occurrence}")
    # Keep merge_generation until its own 24-hour TTL. Personal meeting rooms
    # reuse meeting_id, and a revoked task from the prior session can still be
    # delivered after this cleanup. Resetting the counter would let that stale
    # task collide with generation 1 of the new session and release its bot.
    if not preserve_lock:
        suffixes.extend(["starting_lock", "capture_authorization_pending"])
    for suffix in suffixes:
        redis.delete(f"recall:meeting:{meeting_id}:{suffix}")


def handle_meeting_ended(
    payload: dict[str, Any],
    before_side_effect: SideEffectBoundary | None = None,
):
    """meeting.ended is the canonical wind-down. Revoke any pending grace
    task, stop+leave the bot if one exists, and clear all per-meeting
    state. When no bot exists, the meeting ended without ever recording —
    the expected outcome when the doctor never joined.

    Zoom's occurrence UUID, rather than the reusable PMR meeting ID, is the
    authority for teardown. A matching occurrence can end immediately even
    inside the legacy 60-second start fence; a delayed prior-occurrence event
    cannot alter the new occurrence.
    """
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")
    meeting_id_str = str(meeting_id)
    event_occurrence = _occurrence_digest(obj)

    redis = get_redis()
    merge_task_id: str | None = None
    with _lifecycle_lock(redis, meeting_id_str):
        active_occurrence = _rget(
            redis, f"recall:meeting:{meeting_id}:occurrence_digest"
        )
        if active_occurrence is not None and event_occurrence != active_occurrence:
            log.warning(
                "meeting.ended fenced from a different occurrence",
                meeting_ref=provider_log_ref(meeting_id_str),
                event_has_occurrence=event_occurrence is not None,
            )
            return
        if active_occurrence is None and event_occurrence is not None:
            has_legacy_state = any(
                redis.get(f"recall:meeting:{meeting_id}:{suffix}")
                for suffix in ("doctor_id", "host_id", "bot_id")
            )
            if not has_legacy_state:
                log.info(
                    "meeting.ended ignored without active occurrence state",
                    meeting_ref=provider_log_ref(meeting_id_str),
                )
                return
            # Rolling compatibility: old workers did not write an occurrence
            # key. Adopt the UUID carried by the end event before mutation.
            redis.set(
                f"recall:meeting:{meeting_id}:occurrence_digest",
                event_occurrence,
                ex=timedelta(hours=24),
            )
            active_occurrence = event_occurrence
        elif (
            active_occurrence is None
            and event_occurrence is None
            and redis.get(f"recall:meeting:{meeting_id}:starting_lock")
        ):
            # Old payload + old state: retain the pre-cutover stale-event guard.
            log.info(
                "legacy meeting.ended skipped inside starting lock window",
                meeting_ref=provider_log_ref(meeting_id_str),
            )
            return

        bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
        doctor_id = _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")
        merge_task_id = _rget(redis, f"recall:meeting:{meeting_id}:merge_task_id")
        merge_release_intent_id = _rget(
            redis, f"recall:meeting:{meeting_id}:merge_release_intent_id"
        )
        teardown_intent = None

        if bot_id:
            generation_value = _rget(
                redis, f"recall:meeting:{meeting_id}:merge_generation"
            )
            teardown_intent = store_zoom_teardown_intent(
                action="release",
                meeting_id=meeting_id_str,
                bot_id=bot_id,
                occurrence_digest=active_occurrence,
                generation=(
                    int(generation_value) if generation_value is not None else None
                ),
                stop_recording=True,
                cleanup_session=True,
                doctor_id=doctor_id,
                mark_meeting_ended=True,
            )
            _begin_side_effect(before_side_effect)
            log.info(
                "meeting ended with tracked bot",
                meeting_ref=provider_log_ref(meeting_id_str),
                bot_ref=provider_log_ref(bot_id),
            )
            try:
                recall.stop_recording(bot_id)
            except Exception as exc:
                # A failed stop must not prevent the stronger teardown call.
                # If leave succeeds, Recall has confirmed the bot is gone and
                # local ownership can safely be released.
                log.warning(
                    "stop_recording failed during meeting.ended; attempting leave",
                    bot_ref=provider_log_ref(bot_id),
                    meeting_ref=provider_log_ref(str(meeting_id)),
                    error_type=type(exc).__name__,
                )
            try:
                recall.leave_call(bot_id)
            except Exception as exc:
                log.warning(
                    "leave_call failed during meeting.ended; retaining bot state",
                    bot_ref=provider_log_ref(bot_id),
                    meeting_ref=provider_log_ref(str(meeting_id)),
                    error_type=type(exc).__name__,
                )
                # Do not revoke the expiry task, clear the provider handle, or
                # mark the meeting ended without a confirmed leave.  Raising
                # crosses dispatch's ambiguity fence and feeds health alerts.
                raise
        else:
            log.info(
                "meeting ended without recording",
                meeting_ref=provider_log_ref(str(meeting_id)),
            )

        # Provider departure is confirmed before the local status transition;
        # the encrypted release intent retains doctor_id so reconciliation can
        # retry this idempotent database write without depending on Redis.
        if merge_window.mark_meeting_ended(doctor_id, meeting_id_str):
            log.info(
                "zoom meeting status updated to ended",
                meeting_ref=provider_log_ref(str(meeting_id)),
            )

        _clear_meeting_keys(redis, meeting_id)

        if teardown_intent is not None:
            complete_zoom_teardown_intent(teardown_intent.delivery_id)
            if merge_release_intent_id is not None:
                complete_zoom_teardown_intent(merge_release_intent_id)

    _revoke_merge_window_task(merge_task_id, meeting_id_str)


HANDLERS = {
    "meeting.started": handle_meeting_started,
    "meeting.participant_joined": handle_participant_joined,
    "meeting.participant_left": handle_participant_left,
    "meeting.ended": handle_meeting_ended,
}


def _log_payload_shape(event_type: str, payload: dict[str, Any]) -> None:
    """TEMPORARY probe (added 2026-08-13). Confirms whether Zoom sends `uuid`
    and `host_id` at the object level on *every* meeting event, not just
    meeting.started.

    If it does, per-meeting Redis state can be namespaced by the session
    `uuid` — unique per meeting instance, unlike `id`, which personal meeting
    rooms reuse. That would let handle_meeting_started stop clearing stale
    keys entirely, which is what forces every handler to race against the
    cleanup today. `host_id` on participant_joined would additionally remove
    the Redis/DB lookup in _resolve_host_id.

    Logs only fixed-schema booleans, never provider-controlled field names or
    values, so no participant name or email can reach the log pipeline. Remove
    once one real production meeting has been observed — the answer does not
    change per meeting.
    """
    obj = payload.get("payload", {}).get("object", {})
    if not isinstance(obj, dict):
        log.info("zoom payload shape: object missing", event_type=event_type)
        return

    participant = obj.get("participant")
    log.info(
        "zoom payload shape",
        event_type=event_type,
        has_uuid="uuid" in obj,
        has_host_id="host_id" in obj,
        has_participant=isinstance(participant, dict),
        participant_has_id=isinstance(participant, dict) and "id" in participant,
        participant_has_user_id=(
            isinstance(participant, dict) and "user_id" in participant
        ),
    )


def dispatch(
    event_type: str,
    payload: dict[str, Any],
    *,
    provider_event_digest: str | None = None,
):
    _log_payload_shape(event_type, payload)

    handler = HANDLERS.get(event_type)
    if not handler:
        log.info("no handler for zoom event type", event_type=event_type)
        return
    if provider_event_digest is None:
        handler(payload)
        return

    claim = zoom_webhook_operations.claim_event(
        provider_event_digest=provider_event_digest,
        event_type=event_type,
    )
    if claim.disposition == "succeeded":
        if event_type == "meeting.started":
            _arm_started_watchdog_after_success(payload)
        return
    if claim.disposition == "busy":
        raise RetryableWebhookError("handler_error")
    if claim.disposition == "uncertain":
        raise PermanentWebhookError("provider_operation_uncertain")

    side_effect_started = False

    def begin_once() -> None:
        nonlocal side_effect_started
        if side_effect_started:
            return
        if not zoom_webhook_operations.begin_side_effect(claim):
            raise PermanentWebhookError("provider_operation_uncertain")
        side_effect_started = True

    try:
        handler(payload, before_side_effect=begin_once)
    except (RetryableWebhookError, PermanentWebhookError) as exc:
        ambiguous = zoom_webhook_operations.fail_event(
            claim,
            permanent=isinstance(exc, PermanentWebhookError),
        )
        if ambiguous:
            raise PermanentWebhookError("provider_operation_uncertain") from None
        raise
    except Exception:
        ambiguous = zoom_webhook_operations.fail_event(claim)
        if ambiguous:
            raise PermanentWebhookError("provider_operation_uncertain") from None
        raise

    if not zoom_webhook_operations.complete_event(claim):
        raise PermanentWebhookError("provider_operation_uncertain")
    if event_type == "meeting.started":
        _arm_started_watchdog_after_success(payload)
