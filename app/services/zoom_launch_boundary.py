"""Original lifecycle-token fencing for one already-authorized Zoom launch."""

import time
from datetime import UTC, datetime

from typeid import TypeID

from activemodel.session_manager import get_session
from app.models.external_webhook_delivery import ExternalWebhookDelivery
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from sqlalchemy import text, update
from sqlmodel import col

PUBLICATION_RESERVE_SECONDS = 5.0
# Full existing consent window plus bounded Create, pre-read, Start and confirm.
LAUNCH_NETWORK_BUDGET_SECONDS = 30.0 + 45.0 + 5.0 + 30.0 + 15.0

_TOKEN_TTL = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
 or redis.call('GET', KEYS[2]) ~= ARGV[2]
 or redis.call('GET', KEYS[3]) ~= ARGV[3] then return -1 end
if ARGV[4] == '' then
 if redis.call('EXISTS', KEYS[4]) ~= 0 then return -1 end
elseif redis.call('GET', KEYS[4]) ~= ARGV[4] then return -1 end
return redis.call('PTTL', KEYS[1])
"""
_PUBLISH_BOT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
 or redis.call('PTTL', KEYS[1]) < 1
 or redis.call('GET', KEYS[2]) ~= ARGV[2]
 or redis.call('GET', KEYS[3]) ~= ARGV[3]
 or redis.call('EXISTS', KEYS[4]) ~= 0 then return 0 end
redis.call('SET', KEYS[4], ARGV[4], 'EX', 86400)
return 1
"""
_PUBLISH_RECORDING = """
if redis.call('GET', KEYS[1]) ~= ARGV[1]
 or redis.call('PTTL', KEYS[1]) < 1
 or redis.call('GET', KEYS[2]) ~= ARGV[2]
 or redis.call('GET', KEYS[3]) ~= ARGV[3]
 or redis.call('GET', KEYS[4]) ~= ARGV[4] then return 0 end
for i=5,7 do
 if redis.call('EXISTS', KEYS[i]) ~= 0 then return 0 end
end
redis.call('SET', KEYS[5], ARGV[3], 'EX', 86400)
redis.call('SET', KEYS[6], ARGV[5], 'EX', 86400)
redis.call('SET', KEYS[7], ARGV[6], 'EX', 86400)
redis.call('SET', KEYS[8], '1', 'EX', 86400)
redis.call('SET', KEYS[9], '1', 'EX', 86400)
return 1
"""


class ZoomLaunchBoundary:
    def __init__(self, redis, lock, meeting_id: str, occurrence: str, doctor_id: str):
        self.redis = redis
        self.prefix = f"recall:meeting:{meeting_id}"
        self.lock_key = self.prefix + ":lifecycle_lock"
        self.token = getattr(getattr(lock, "local", None), "token", None)
        if (
            not self.token
            or lock.name != self.lock_key
            or not occurrence
            or not doctor_id
        ):
            raise ValueError("zoom_launch_lock_unconfirmed")
        self.occurrence = occurrence
        self.doctor_id = doctor_id
        self.bot_id = None
        sampled = time.monotonic()
        ttl = self._ttl()
        if not isinstance(ttl, int) or ttl <= 0 or ttl > 180000:
            raise ValueError("zoom_launch_lock_unconfirmed")
        self.deadline = sampled + ttl / 1000.0

    def _ttl(self):
        return self.redis.eval(
            _TOKEN_TTL,
            4,
            *self._keys(),
            self.token,
            self.occurrence,
            self.doctor_id,
            self.bot_id or "",
        )

    @property
    def operation_deadline(self) -> float:
        return self.deadline - PUBLICATION_RESERVE_SECONDS

    def require(self, network_seconds: float = 0.0) -> None:
        sampled = time.monotonic()
        ttl = self._ttl()
        if not isinstance(ttl, int) or ttl <= 0:
            raise ValueError("zoom_launch_lock_lost")
        remaining = min(self.deadline, sampled + ttl / 1000.0) - time.monotonic()
        if remaining <= network_seconds + PUBLICATION_RESERVE_SECONDS:
            raise TimeoutError("zoom_launch_budget_exhausted")

    def _keys(self):
        return [
            self.lock_key,
            self.prefix + ":occurrence_digest",
            self.prefix + ":doctor_id",
            self.prefix + ":bot_id",
        ]

    def publish_bot(self, bot_id: str) -> None:
        self.require()
        if (
            self.redis.eval(
                _PUBLISH_BOT,
                4,
                *self._keys(),
                self.token,
                self.occurrence,
                self.doctor_id,
                bot_id,
            )
            != 1
        ):
            raise ValueError("zoom_launch_publication_refused")
        self.bot_id = bot_id

    def complete(self, delivery_id: str, meeting_id: str, appointment_id: str) -> None:
        """Retire only this pending launch and mark the same occurrence started."""
        self.require()
        now = datetime.now(UTC)
        with get_session() as session:
            # A row lock wait cannot silently consume the publication reserve.
            session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
            session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            retired = session.execute(
                update(ExternalWebhookDelivery)
                .where(
                    col(ExternalWebhookDelivery.id) == TypeID.from_string(delivery_id),
                    col(ExternalWebhookDelivery.provider) == "zoom",
                    col(ExternalWebhookDelivery.event_type)
                    == "internal.zoom_bot_release",
                    col(ExternalWebhookDelivery.state).in_(["pending", "retry_wait"]),
                )
                .values(
                    state="completed",
                    processed_at=now,
                    encrypted_payload=None,
                    payload_purged_at=now,
                    lease_expires_at=None,
                    wakeup_after=None,
                    last_error_code=None,
                )
                .returning(col(ExternalWebhookDelivery.id))
            )
            if len(retired.all()) != 1:
                raise ValueError("zoom_launch_teardown_already_claimed")
            self.require()
            started = session.execute(
                update(ZoomMeeting)
                .where(
                    col(ZoomMeeting.doctor_id) == self.doctor_id,
                    col(ZoomMeeting.zoom_meeting_id) == meeting_id,
                    col(ZoomMeeting.appointment_id) == appointment_id,
                    col(ZoomMeeting.capture_authorization_occurrence_digest)
                    == self.occurrence,
                    col(ZoomMeeting.capture_authorization_consumed_at).is_not(None),
                    col(ZoomMeeting.status).in_(
                        [ZoomMeetingStatus.scheduled, ZoomMeetingStatus.started]
                    ),
                )
                .values(status=ZoomMeetingStatus.started)
                .returning(col(ZoomMeeting.id))
            )
            if len(started.all()) != 1:
                raise ValueError("zoom_launch_meeting_changed")
            self.require()
            session.commit()

    def publish_recording(
        self, bot_id: str, recording_id: str, appointment_id: str, patient_id: str
    ) -> None:
        self.require()
        if not recording_id or recording_id == bot_id:
            raise ValueError("zoom_launch_recording_identity_invalid")
        recording = f"recall:recording:{recording_id}"
        keys = self._keys() + [
            recording + ":doctor_id",
            recording + ":appointment_id",
            recording + ":patient_id",
            self.prefix + ":capture_started",
            self.prefix + ":recording_active",
        ]
        if (
            self.redis.eval(
                _PUBLISH_RECORDING,
                len(keys),
                *keys,
                self.token,
                self.occurrence,
                self.doctor_id,
                bot_id,
                appointment_id,
                patient_id,
            )
            != 1
        ):
            raise ValueError("zoom_launch_publication_refused")
