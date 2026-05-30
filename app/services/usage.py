"""Platform-usage queries the boss runs by hand to assess system
health and adoption. Lifted from issue #34's body verbatim where
possible; parameterised where a literal value (LIMIT, since-date) is
clearer as a module constant.

All five queries are read-only. Used by the /admin/usage endpoint."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from activemodel.session_manager import get_session
from sqlalchemy import text

RECENT_APPOINTMENTS_LIMIT = 50
DAILY_VOLUME_LIMIT = 10
TIME_TO_NOTE_SINCE = "2025-04-10"


class RecentAppointmentRow(BaseModel):
    created_at: datetime | None
    doctor_email: str | None
    appointment_name: str | None


class DoctorUsageRow(BaseModel):
    appointment_count: int
    doctor_email: str | None
    last_note_at: datetime | None


class DailyVolumeRow(BaseModel):
    day: date
    appointment_count: int
    note_count: int
    doctor_count: int


class PendingUploadRow(BaseModel):
    audio_at: datetime | None
    transcript_at: datetime | None
    note_at: datetime | None
    appointment_name: str | None


class TimeToNoteRow(BaseModel):
    doctor_email: str | None
    avg_time_to_note_seconds: float | None
    max_time_to_note_seconds: float | None
    min_time_to_note_seconds: float | None
    sample_count: int


def _interval_seconds(value: Any) -> float | None:
    """AVG / MAX / MIN of an interval column comes back as
    datetime.timedelta. Convert to float seconds so the value is
    JSON-serialisable."""
    if value is None:
        return None
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    return float(value)


def recent_appointments() -> list[RecentAppointmentRow]:
    query = text(
        """
        SELECT MAX(avn.created_at) AS created_at,
               MAX(d.email)        AS doctor_email,
               MAX(a.appointment_name) AS appointment_name
        FROM ai_visit_note avn
        JOIN doctor d ON d.id = avn.doctor_id
        JOIN appointment a ON avn.appointment_id = a.id
        GROUP BY avn.appointment_id
        ORDER BY MAX(avn.created_at) DESC
        LIMIT :limit
        """
    )
    with get_session() as session:
        rows = session.execute(query, {"limit": RECENT_APPOINTMENTS_LIMIT}).all()
    return [RecentAppointmentRow(**dict(r._mapping)) for r in rows]


def doctor_usage() -> list[DoctorUsageRow]:
    query = text(
        """
        SELECT COUNT(DISTINCT(avn.appointment_id)) AS appointment_count,
               d.email                              AS doctor_email,
               MAX(avn.created_at)                  AS last_note_at
        FROM ai_visit_note avn
        JOIN doctor d ON d.id = avn.doctor_id
        GROUP BY avn.doctor_id, d.email
        ORDER BY appointment_count DESC
        """
    )
    with get_session() as session:
        rows = session.execute(query).all()
    return [DoctorUsageRow(**dict(r._mapping)) for r in rows]


def daily_volume() -> list[DailyVolumeRow]:
    query = text(
        """
        SELECT DATE(a.created_at)               AS day,
               COUNT(DISTINCT a.id)             AS appointment_count,
               COUNT(avn.id)                    AS note_count,
               COUNT(DISTINCT(avn.doctor_id))   AS doctor_count
        FROM appointment a
        JOIN ai_visit_note avn ON avn.appointment_id = a.id
        GROUP BY day
        ORDER BY appointment_count DESC
        LIMIT :limit
        """
    )
    with get_session() as session:
        rows = session.execute(query, {"limit": DAILY_VOLUME_LIMIT}).all()
    return [DailyVolumeRow(**dict(r._mapping)) for r in rows]


def pending_uploads() -> list[PendingUploadRow]:
    query = text(
        """
        SELECT vaf.created_at        AS audio_at,
               avt.created_at        AS transcript_at,
               avn.created_at        AS note_at,
               a.appointment_name    AS appointment_name
        FROM visit_audio_file vaf
        LEFT JOIN ai_visit_transcript avt ON avt.appointment_id = vaf.appointment_id
        JOIN appointment a ON a.id = vaf.appointment_id
        JOIN ai_visit_note avn ON avn.ai_transcript_id = avt.id
        JOIN doctor d ON vaf.doctor_id = d.id
        ORDER BY vaf.created_at DESC
        """
    )
    with get_session() as session:
        rows = session.execute(query).all()
    return [PendingUploadRow(**dict(r._mapping)) for r in rows]


def time_to_note() -> list[TimeToNoteRow]:
    query = text(
        """
        WITH time_to_notes AS (
          SELECT d.email AS doctor_email,
                 avn.created_at - vaf.created_at AS time_to_note
          FROM visit_audio_file vaf
          LEFT JOIN ai_visit_transcript avt ON avt.appointment_id = vaf.appointment_id
          JOIN ai_visit_note avn ON avn.ai_transcript_id = avt.id
          JOIN doctor d ON vaf.doctor_id = d.id
          WHERE vaf.created_at > :since
        )
        SELECT ttn.doctor_email                  AS doctor_email,
               AVG(ttn.time_to_note)             AS avg_time_to_note,
               MAX(ttn.time_to_note)             AS max_time_to_note,
               MIN(ttn.time_to_note)             AS min_time_to_note,
               COUNT(DISTINCT(ttn.time_to_note)) AS sample_count
        FROM time_to_notes ttn
        GROUP BY doctor_email
        ORDER BY AVG(ttn.time_to_note) DESC
        """
    )
    with get_session() as session:
        rows = session.execute(query, {"since": TIME_TO_NOTE_SINCE}).all()

    return [
        TimeToNoteRow(
            doctor_email=row.doctor_email,
            avg_time_to_note_seconds=_interval_seconds(row.avg_time_to_note),
            max_time_to_note_seconds=_interval_seconds(row.max_time_to_note),
            min_time_to_note_seconds=_interval_seconds(row.min_time_to_note),
            sample_count=row.sample_count,
        )
        for row in rows
    ]
