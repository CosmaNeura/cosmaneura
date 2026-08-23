from typing import Any

import app.jobs.score_engagement  # noqa: E402 — job import intentional
import app.jobs.transcribe as transcribe_job  # noqa: E402 — job import intentional
from app import log
from app.configuration.redis import get_redis
from app.services import recall as recall_service

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor import Doctor


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def handle_recording_done(payload: dict[str, Any]):
    data = payload.get("data", {})
    bot_id = data.get("bot", {}).get("id")
    recording_id = data.get("recording", {}).get("id")

    redis = get_redis()
    doctor_id = _rget(redis, f"recall:recording:{recording_id}:doctor_id") or _rget(
        redis, f"recall:bot:{bot_id}:doctor_id"
    )
    doctor = Doctor.get(id=doctor_id) if doctor_id else None
    if doctor is None or doctor.deleted_at is not None:
        log.warning(
            "recall recording ignored: doctor unavailable",
            bot_id=bot_id,
            recording_id=recording_id,
            doctor_id=doctor_id,
        )
        return

    log.info(
        "recall recording done — kicking off async transcript",
        bot_id=bot_id,
        recording_id=recording_id,
    )
    recall_service.create_async_transcript(recording_id)


def handle_transcript_done(payload: dict[str, Any]):
    data = payload.get("data", {})
    bot_id = (data.get("bot") or {}).get("id")
    recording_id = (data.get("recording") or {}).get("id")
    transcript_id = data.get("transcript", {}).get("id")

    log.info(
        "recall transcript done",
        bot_id=bot_id,
        recording_id=recording_id,
        transcript_id=transcript_id,
    )

    redis = get_redis()
    # recording-scoped keys (new per-patient flow) take precedence over bot-scoped (legacy)
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
            "missing redis keys for recall recording, cannot create transcript",
            bot_id=bot_id,
            recording_id=recording_id,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
        )
        return

    doctor = Doctor.get(id=doctor_id)
    if doctor is None or doctor.deleted_at is not None:
        log.warning(
            "recall transcript ignored: doctor unavailable",
            bot_id=bot_id,
            recording_id=recording_id,
            doctor_id=doctor_id,
        )
        return

    transcript_meta = recall_service.get_transcript(transcript_id)
    download_url = (transcript_meta.get("data") or {}).get("download_url")
    assert download_url, f"no download_url in transcript metadata: {transcript_meta}"

    raw_entries = recall_service.download_transcript_data(download_url)
    log.info("recall transcript downloaded", bot_id=bot_id, segments=len(raw_entries))

    entries = _map_transcript_entries(raw_entries)

    # Recall labels speakers with the Zoom participant name; relabel to
    # Doctor/Patient (same normalization as the audio-upload path) so note
    # generation and engagement scoring can find the patient's turns. Best-effort:
    # if identification fails, keep the raw labels so note generation still runs
    # (engagement scoring will simply skip, as it did before this fix).
    try:
        entries = transcribe_job.label_speakers(entries)
    except Exception as e:
        log.warning("speaker labeling failed, keeping raw labels", error=e)

    duration = _compute_duration_seconds(entries)

    transcript = AIVisitTranscript.find_or_create_by(
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        patient_id=patient_id,
        duration=duration,
        transcript_data=[e.model_dump() for e in entries],
    )

    log.info(
        "recall AIVisitTranscript saved",
        transcript_id=str(transcript.id),
        entries=len(entries),
    )
    transcribe_job.queue_schema_transcription(transcript)

    # Engagement engine v2: score the patient's enacted communication role from
    # this transcript, mirroring the audio-upload path in transcribe.py. Without
    # this, Zoom/Recall-recorded visits never get an EngagementRole and the
    # personality badge never shows for them. Idempotent (one role per
    # transcript) and skips when there's too little patient speech.
    app.jobs.score_engagement.queue(transcript_id=transcript.id)


def _map_transcript_entries(raw_data: list[dict]) -> list[TranscriptEntry]:
    entries = []
    for segment in raw_data:
        speaker = (segment.get("participant") or {}).get("name") or "Unknown"
        words = segment.get("words") or []
        if not words:
            continue
        content = " ".join(w["text"] for w in words)
        start_ms = int(words[0]["start_timestamp"]["relative"] * 1000)
        end_ms = int(words[-1]["end_timestamp"]["relative"] * 1000)
        entries.append(
            TranscriptEntry(
                speaker=speaker, content=content, startTime=start_ms, endTime=end_ms
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
}


def dispatch(event_type: str, payload: dict[str, Any]):
    handler = HANDLERS.get(event_type)
    if not handler:
        log.info("no handler for recall event type", event_type=event_type)
        return
    handler(payload)
