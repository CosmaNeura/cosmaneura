from datetime import datetime

from app import log
from app.celery import celery_app
from app.services import zoom as zoom_service

from app.models.doctor import Doctor
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus


def perform(doctor_id: str) -> None:
    """Fetch upcoming Zoom meetings for a doctor and upsert into the DB."""
    doctor = Doctor.get(id=doctor_id)

    if (
        not doctor
        or doctor.deleted_at is not None
        or not zoom_service.get_zoom_integration(doctor)
    ):
        log.info(
            "skipping zoom sync: doctor unavailable or no zoom integration",
            doctor_id=doctor_id,
        )
        return

    meetings = zoom_service.get_user_meetings(doctor)

    synced = 0
    skipped_no_start_time = 0

    for m in meetings:
        zoom_meeting_id = str(m["id"])

        # Recurring meetings with no fixed time (Zoom meeting type 3) arrive
        # without start_time or duration. ZoomMeeting requires both, and the
        # agenda endpoint filters and sorts on start_time, so there is nothing
        # useful to store. Skip them.
        #
        # Reading m["start_time"] directly used to raise KeyError here, and
        # because each row is saved inside the loop, the sync died partway:
        # meetings before the untimed one persisted, everything after it was
        # dropped, and the task burned all 20 autoretries. That silently
        # truncated doctors' agendas rather than failing visibly.
        raw_start_time = m.get("start_time")
        if not raw_start_time:
            skipped_no_start_time += 1
            continue

        start_time = raw_start_time
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))

        topic = m.get("topic") or ""
        join_url = m.get("join_url") or ""
        duration = m.get("duration") or 0

        existing = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=zoom_meeting_id)

        if existing:
            if existing.status in (ZoomMeetingStatus.started, ZoomMeetingStatus.ended):
                # Don't reset status for active/completed meetings; only refresh mutable fields
                existing.topic = topic
                existing.join_url = join_url
            else:
                existing.topic = topic
                existing.start_time = start_time
                existing.duration = duration
                existing.join_url = join_url
            existing.save()
        else:
            ZoomMeeting(
                doctor_id=doctor.id,
                zoom_meeting_id=zoom_meeting_id,
                topic=topic,
                start_time=start_time,
                duration=duration,
                join_url=join_url,
            ).save()

        synced += 1

    log.info(
        "zoom meetings synced",
        doctor_id=doctor_id,
        count=synced,
        fetched=len(meetings),
        skipped_no_start_time=skipped_no_start_time,
    )


perform_celery = celery_app.task(perform)


def queue(doctor_id: str) -> None:
    "always use this method to queue the sync job"
    perform_celery.delay(doctor_id)
