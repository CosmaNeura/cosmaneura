from datetime import datetime, timezone

from app import log
from app.celery import celery_app
from app.models.doctor import Doctor
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from app.services import zoom as zoom_service


def perform(doctor_id: str) -> None:
    """Fetch upcoming Zoom meetings for a doctor and upsert into the DB."""
    doctor = Doctor.get(id=doctor_id)

    if not doctor or not doctor.zoom_user_id:
        log.info("skipping zoom sync: no doctor or zoom_user_id", doctor_id=doctor_id)
        return

    meetings = zoom_service.get_user_meetings(doctor)

    for m in meetings:
        zoom_meeting_id = str(m["id"])
        start_time = m["start_time"]
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))

        existing = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=zoom_meeting_id)

        if existing:
            if existing.status in (ZoomMeetingStatus.started, ZoomMeetingStatus.ended):
                # Don't reset status for active/completed meetings; only refresh mutable fields
                existing.topic = m["topic"]
                existing.join_url = m["join_url"]
            else:
                existing.topic = m["topic"]
                existing.start_time = start_time
                existing.duration = m["duration"]
                existing.join_url = m["join_url"]
            existing.save()
        else:
            ZoomMeeting(
                doctor_id=doctor.id,
                zoom_meeting_id=zoom_meeting_id,
                topic=m["topic"],
                start_time=start_time,
                duration=m["duration"],
                join_url=m["join_url"],
            ).save()

    log.info("zoom meetings synced", doctor_id=doctor_id, count=len(meetings))


perform_celery = celery_app.task(perform)


def queue(doctor_id: str) -> None:
    "always use this method to queue the sync job"
    perform_celery.delay(doctor_id)
