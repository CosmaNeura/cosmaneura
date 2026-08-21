"""Recall adapter handlers invoked only from the durable webhook inbox."""

from typing import Any

import app.jobs.transcribe as transcribe_job
from app import log
from app.configuration.redis import get_redis
from app.services import recall as recall_service
from app.services import recall_webhook_operations
from app.services.external_webhook_inbox import (
    PermanentWebhookError,
    RetryableWebhookError,
    provider_log_ref,
)

from app.models.ai_visit_transcript import TranscriptEntry


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def _required_id(data: dict[str, Any], key: str) -> str:
    item = data.get(key)
    value = item.get("id") if isinstance(item, dict) else None
    if value is None:
        raise PermanentWebhookError()
    return str(value)


def handle_recording_done(payload: dict[str, Any]) -> None:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PermanentWebhookError()
    recording_id = _required_id(data, "recording")
    reservation = recall_webhook_operations.reserve_transcript_creation(recording_id)

    log.info(
        "recall recording done",
        recording_ref=provider_log_ref(recording_id),
        operation_disposition=reservation.disposition,
    )
    if reservation.disposition == "succeeded":
        return
    if reservation.disposition == "uncertain":
        raise RetryableWebhookError("provider_operation_uncertain")
    try:
        recall_service.create_async_transcript(recording_id)
    except Exception:
        # A timeout/transport failure may have happened after Recall accepted
        # the mutation. Do not repeat it blindly; a later signed transcript
        # callback reconciles this receipt.
        recall_webhook_operations.mark_transcript_creation_uncertain(
            reservation.operation_id
        )
        raise RetryableWebhookError("provider_operation_uncertain") from None
    recall_webhook_operations.mark_transcript_creation_succeeded(
        reservation.operation_id
    )


def handle_transcript_done(payload: dict[str, Any]) -> None:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PermanentWebhookError()
    bot_id = _required_id(data, "bot")
    recording_id = _required_id(data, "recording")
    transcript_id = _required_id(data, "transcript")

    recall_webhook_operations.confirm_transcript_creation(recording_id)
    replay = recall_webhook_operations.find_ingested_transcript(transcript_id)
    if replay is not None:
        recall_webhook_operations.dispatch_pipeline(replay.operation_id)
        log.info(
            "recall transcript replayed from durable receipt",
            transcript_ref=provider_log_ref(transcript_id),
        )
        return

    redis = get_redis()
    # Recording-scoped keys (new per-patient flow) take precedence over the
    # bot-scoped legacy keys.  These bindings remain the known Phase 2
    # durability gap; absence must retry, never silently complete the inbox.
    doctor_id = _rget(redis, f"recall:recording:{recording_id}:doctor_id") or _rget(
        redis, f"recall:bot:{bot_id}:doctor_id"
    )
    appointment_id = _rget(
        redis, f"recall:recording:{recording_id}:appointment_id"
    ) or _rget(redis, f"recall:bot:{bot_id}:appointment_id")
    patient_id = _rget(redis, f"recall:recording:{recording_id}:patient_id") or _rget(
        redis, f"recall:bot:{bot_id}:patient_id"
    )
    if not doctor_id or not appointment_id or not patient_id:
        log.warning(
            "recall transcript tenant mapping unavailable",
            bot_ref=provider_log_ref(bot_id),
            recording_ref=provider_log_ref(recording_id),
        )
        raise RetryableWebhookError("tenant_mapping_unavailable")

    transcript_meta = recall_service.get_transcript(transcript_id)
    transcript_data = transcript_meta.get("data")
    download_url = (
        transcript_data.get("download_url")
        if isinstance(transcript_data, dict)
        else None
    )
    if not isinstance(download_url, str) or not download_url.startswith("https://"):
        raise RetryableWebhookError("handler_error")
    raw_entries = recall_service.download_transcript_data(download_url)
    log.info(
        "recall transcript downloaded",
        transcript_ref=provider_log_ref(transcript_id),
        segments=len(raw_entries),
    )

    entries = _map_transcript_entries(raw_entries)
    try:
        entries = transcribe_job.label_speakers(entries)
    except Exception as exc:
        log.warning(
            "speaker labeling failed, keeping provider labels",
            error_type=type(exc).__name__,
        )

    try:
        result = recall_webhook_operations.persist_transcript(
            provider_transcript_id=transcript_id,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
            duration=_compute_duration_seconds(entries),
            entries=entries,
        )
    except recall_webhook_operations.RecallOperationConflict:
        raise PermanentWebhookError() from None

    log.info(
        "recall canonical transcript persisted",
        transcript_ref=provider_log_ref(transcript_id),
        disposition=result.disposition,
        entries=len(entries),
    )
    recall_webhook_operations.dispatch_pipeline(result.operation_id)


def handle_transcript_processing(payload: dict[str, Any]) -> None:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PermanentWebhookError()
    recording_id = _required_id(data, "recording")
    transcript_id = _required_id(data, "transcript")
    recall_webhook_operations.confirm_transcript_creation(recording_id)
    log.info(
        "recall transcript status received",
        event_type="transcript.processing",
        transcript_ref=provider_log_ref(transcript_id),
    )


def handle_transcript_failed(payload: dict[str, Any]) -> None:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PermanentWebhookError()
    recording_id = _required_id(data, "recording")
    transcript_id = _required_id(data, "transcript")
    recall_webhook_operations.mark_transcript_creation_failed(recording_id)
    log.warning(
        "recall transcript failed",
        transcript_ref=provider_log_ref(transcript_id),
    )
    raise PermanentWebhookError("provider_transcript_failed")


def _map_transcript_entries(raw_data: list[dict]) -> list[TranscriptEntry]:
    entries = []
    for segment in raw_data:
        participant = segment.get("participant")
        speaker = (
            participant.get("name")
            if isinstance(participant, dict) and participant.get("name")
            else "Unknown"
        )
        words = segment.get("words") or []
        if not isinstance(words, list) or not words:
            continue
        try:
            content = " ".join(str(word["text"]) for word in words)
            start_ms = int(words[0]["start_timestamp"]["relative"] * 1000)
            end_ms = int(words[-1]["end_timestamp"]["relative"] * 1000)
        except (KeyError, TypeError, ValueError):
            raise PermanentWebhookError() from None
        entries.append(
            TranscriptEntry(
                speaker=str(speaker),
                content=content,
                startTime=start_ms,
                endTime=end_ms,
            )
        )
    return entries


def _compute_duration_seconds(entries: list[TranscriptEntry]) -> int:
    if not entries:
        return 0
    return entries[-1].endTime // 1000


HANDLERS = {
    "recording.done": handle_recording_done,
    "transcript.done": handle_transcript_done,
    "transcript.failed": handle_transcript_failed,
    "transcript.processing": handle_transcript_processing,
}


def dispatch(event_type: str, payload: dict[str, Any]) -> None:
    handler = HANDLERS.get(event_type)
    if not handler:
        log.info("no handler for recall event type", event_type=event_type)
        return
    handler(payload)
