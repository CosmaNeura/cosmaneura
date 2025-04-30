#!/usr/bin/env -S ipython -i
# Description: production REPL, not designed to be used locally. `./console.py` in production.
# ruff: noqa: F401, F821

import json
import re
from datetime import datetime, timedelta
from urllib.request import urlopen

from typeid import TypeID

# autoimport packages
import app.commands
import app.jobs
import app.models

# helpful preloaded shortcuts
from app import log
from app.configuration.clerk import clerk  # noqa: F401

from activemodel.utils import find_all_sqlmodels
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

# make all sqlmodels available in the repl
globals().update(find_all_sqlmodels(app.models))


def read_data_url(url):
    "read a url with json, csv, etc and return the data"

    # convert start gist URLS to raw URLs
    if re.match(r"https?://gist\.github\.com/", url):
        url = url.replace("gist.github.com", "gist.githubusercontent.com") + "/raw"

    return urlopen(url).read()


def get_unprocessed_visit_audio_files(session):
    ten_days_ago = datetime.now() - timedelta(days=10)

    stmt = (
        select(VisitAudioFile)
        .outerjoin(
            AIVisitTranscript,
            and_(
                VisitAudioFile.appointment_id == AIVisitTranscript.appointment_id,
                VisitAudioFile.doctor_id == AIVisitTranscript.doctor_id,
            ),
        )
        .where(AIVisitTranscript.id.is_(None))
        .where(VisitAudioFile.azure_file_name.isnot(None))  # Ensure audio file exists
        .where(VisitAudioFile.created_at >= ten_days_ago)
    )
    return session.execute(stmt).scalars().all()


def process_unprocessed_audio_files(audio_files: list[VisitAudioFile]) -> None:
    import app.jobs.transcribe

    for audio_file in audio_files:
        app.jobs.transcribe.perform(str(audio_file.id))


def process_appointments_without_notes(data):
    """Process appointment data and summarize transcripts with note schemas.

    Args:
        data (list): List of appointment dictionaries containing transcript_id and note_schemas.

    Notes:
        Logs empty transcript_id or note_schemas and skips processing when either is missing.
        Calls summarize.perform for each valid transcript_id and schema_id pair.

    Here's the SQL used to generate the content for this query:

    SELECT
        a.id AS appointment_id,
        a.appointment_date,
        a.appointment_name,
        avt.id AS transcript_id,
        JSON_AGG(DISTINCT dns.id)::TEXT AS note_schemas,  -- Cast JSON to TEXT for proper escaping
        JSON_AGG(DISTINCT d.email)::TEXT AS doctors      -- Cast JSON to TEXT for proper escaping
    FROM appointment a
    JOIN visit_audio_file vaf ON a.id = vaf.appointment_id
    LEFT JOIN ai_visit_note avn ON a.id = avn.appointment_id
    LEFT JOIN ai_visit_transcript avt ON a.id = avt.appointment_id
    LEFT JOIN doctor_note_schema dns ON dns.doctor_id = a.doctor_id
    JOIN doctor d ON a.doctor_id = d.id
    WHERE vaf.azure_file_name IS NOT NULL
    AND avn.id IS NULL
    AND d.email != 'info@cliffsidedev.com'
    AND d.email NOT LIKE '%cosmaneura.com'
    AND d.email IS NOT NULL
    GROUP BY a.id, a.appointment_date, a.appointment_name, avt.id;
    """
    import app.jobs.summarize

    for appointment in data:
        transcript_id = appointment.get("transcript_id")
        note_schemas = json.loads(appointment["note_schemas"])

        if not transcript_id:
            log.info(
                f"Empty transcript_id for appointment {appointment['appointment_id']}"
            )
            continue

        if not note_schemas:
            log.info(
                f"Empty note_schemas for appointment {appointment['appointment_id']} with transcript {transcript_id}"
            )
            continue

        for schema_id in note_schemas:
            app.jobs.summarize.perform(transcript_id, schema_id)
