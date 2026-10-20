"""The clinical pipeline end to end: audio -> transcript -> note -> de-identified.

Each stage is unit tested on its own. What nothing covered is the property that
spans them: PHI enters at transcription, is used to generate a note, and is then
removed from the row that held it, leaving a de-identified copy that cannot be
reattached to the visit it came from.

Three external boundaries are stubbed — the transcription provider, the note
LLM, and Azure de-identification. Everything between them is the real job code
against the real database, so the assertions below are about this repository's
behaviour, not about the stubs.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

import pytest

from app.jobs import (
    anonymize,
    extract_medications,
    score_engagement,
    summarize,
    transcribe,
)

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.visit_audio_file import VisitAudioFile
from sqlalchemy import Table, inspect

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory

# Distinctive enough that a substring search for them cannot match incidental
# text, so "the PHI is gone" means what it says.
PATIENT_NAME = "Marguerite Featherstonehaugh"
CLINIC_NAME = "Saint Brendan of Clonfert Memorial"
VISIT_DATE = "the 14th of November 2023"

TRANSCRIPT_PHI = (
    f"{PATIENT_NAME} was seen at {CLINIC_NAME} on {VISIT_DATE}.",
    f"{PATIENT_NAME} reports the shortness of breath has eased since her last visit.",
)

# What the real Azure service would return for the sentences above: the clinical
# content survives, the identifiers do not.
REDACTED = {
    TRANSCRIPT_PHI[0]: "[PATIENT] was seen at [HOSPITAL] on [DATE].",
    TRANSCRIPT_PHI[
        1
    ]: "[PATIENT] reports the shortness of breath has eased since her last visit.",
}

GENERATED_NOTE = {
    "CC": "Shortness of breath, improving.",
    "A/P": "Continue current therapy. Follow up in six weeks.",
}


def _phi_entries() -> list[TranscriptEntry]:
    return [
        TranscriptEntry(
            speaker="Doctor", content=TRANSCRIPT_PHI[0], startTime=0, endTime=4
        ),
        TranscriptEntry(
            speaker="Patient", content=TRANSCRIPT_PHI[1], startTime=4, endTime=9
        ),
    ]


@dataclass
class Pipeline:
    appointment: object
    schema: object
    audio: object
    deidentified: list[str]
    """Every string handed to `deidentify`, in call order."""


@pytest.fixture
def pipeline(monkeypatch):
    """A doctor with one note schema, one uploaded recording, and stubbed externals.

    `doctor_access_in_force` is forced true rather than constructing an entitled
    practice: the gate has its own suite in `doctor_access_gates_test.py`, and
    leaving it live here would make this test fail for reasons that have nothing
    to do with the pipeline.

    Tests using this fixture also take `global_session`, because `summarize`
    commits through its own `get_session()` and the note is otherwise invisible
    to the test's transaction. In production that write lands in a separate
    worker process and is visible without it. The consequence worth knowing is
    that sharing one session means this test cannot detect a genuine
    cross-session visibility failure; it covers the pipeline's behaviour, not
    the session topology.
    """

    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now(timezone.utc),
    ).save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="SOAP",
        fields={"CC": "Chief complaint", "A/P": "Assessment and plan"},
    ).save()
    audio = VisitAudioFile(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        azure_file_name="visit-recording.wav",
    ).save()

    for module in (transcribe, summarize):
        monkeypatch.setattr(module, "doctor_access_in_force", lambda _doctor: True)

    monkeypatch.setattr(
        transcribe,
        "transcribe_audio",
        lambda _audio: transcribe.TranscriptionResult(
            transcript=_phi_entries(), duration=540
        ),
    )
    monkeypatch.setattr(
        summarize, "generate_clinical_note", lambda *_a, **_k: dict(GENERATED_NOTE)
    )
    # Recorded rather than keyed-and-strict: a `REDACTED[text]` lookup would
    # raise KeyError on any content the pipeline normalised before the call,
    # failing with a stack trace instead of an assertion. The call log below is
    # asserted directly, so the constraint is explicit rather than implied.
    deidentified: list[str] = []

    def _record_deidentify(text: str) -> str:
        deidentified.append(text)
        return REDACTED.get(text, text)

    monkeypatch.setattr(anonymize, "deidentify", _record_deidentify)

    # Queued off the same transcript but not part of this pipeline; both reach
    # external services of their own.
    monkeypatch.setattr(score_engagement, "queue", lambda **_k: None)
    monkeypatch.setattr(extract_medications, "queue", lambda **_k: None)

    return Pipeline(
        appointment=appointment,
        schema=schema,
        audio=audio,
        deidentified=deidentified,
    )


def _retained_count() -> int:
    # Counted globally, and compared as a delta by the callers: this table
    # deliberately carries no foreign key to scope a query by, which is the
    # property `test_deidentified_copy_has_no_column...` pins.
    return len(list(AIVisitTranscriptNonPHI.all()))


def _note_for(appointment) -> AIVisitNote | None:
    # Queried by appointment, the way `summarize_merge_test.py` does it; the
    # transcript link is then asserted in Python.
    return cast(
        AIVisitNote | None,
        AIVisitNote.where(AIVisitNote.appointment_id == appointment.id).first(),
    )


def _transcribe(audio) -> AIVisitTranscript:
    transcribe.perform(audio.id)
    transcript = AIVisitTranscript.get(appointment_id=audio.appointment_id)
    assert transcript, "transcription produced no transcript"
    return transcript


def test_pipeline_produces_a_note_then_tombstones_the_transcript(
    sync_celery, global_session, pipeline
):
    """The whole chain, and the state each stage is supposed to leave behind."""

    transcript = _transcribe(pipeline.audio)
    assert [entry.content for entry in transcript.transcript_data] == list(
        TRANSCRIPT_PHI
    )
    assert transcript.deleted_at is None

    # `sync_celery` makes transcribe's queued summarisation run inline, so the
    # note below is produced by the real chain rather than by calling summarize
    # directly.
    note = _note_for(pipeline.appointment)
    assert note, "transcription did not chain into summarisation"
    assert note.ai_transcript_id == transcript.id
    assert note.note == GENERATED_NOTE
    assert note.doctor_note_schema_id == pipeline.schema.id
    assert note.appointment_id == pipeline.appointment.id

    anonymize.perform(transcript.id)

    tombstoned = AIVisitTranscript.get(id=transcript.id)
    assert tombstoned
    assert tombstoned.transcript_data == []
    assert tombstoned.deleted_at is not None


def test_deidentified_copy_carries_none_of_the_transcript_phi(
    sync_celery, global_session, pipeline
):
    """The point of the whole exercise."""

    transcript = _transcribe(pipeline.audio)
    before = _retained_count()

    anonymize.perform(transcript.id)

    copies = list(AIVisitTranscriptNonPHI.all())
    assert len(copies) - before == 1, "de-identification produced no retained copy"
    retained = " ".join(entry.content for entry in copies[-1].data)

    # The pipeline routed each transcript entry through de-identification, in
    # order, with its content unaltered.
    assert pipeline.deidentified == list(TRANSCRIPT_PHI)

    assert retained, "the retained copy is empty, so the assertions below prove nothing"
    for identifier in (PATIENT_NAME, CLINIC_NAME, VISIT_DATE):
        assert identifier not in retained
    # The clinical substance has to survive, or de-identification has merely
    # destroyed the dataset it exists to build.
    assert "shortness of breath" in retained


def test_deidentified_copy_has_no_column_that_could_reattach_it_to_the_visit(pipeline):
    """`AIVisitTranscriptNonPHI` documents this; nothing enforced it.

    Foreign keys or timestamps would let relationship or timing metadata link a
    retained row back to the visit it was derived from, which is the one thing
    this table must not permit.
    """

    # An exact set rather than a list of forbidden names, deliberately. This
    # table exists to be published as a de-identified dataset, so any new column
    # is a re-identification question and not only the obvious foreign keys and
    # timestamps: a locale or a duration can single out a visit in a small
    # cohort. Adding one should fail here and be argued for, not slip past a
    # blocklist that nobody remembered to extend.
    assert set(
        cast(Table, inspect(AIVisitTranscriptNonPHI).local_table).columns.keys()
    ) == {"id", "data"}


def test_note_outlives_the_phi_it_was_generated_from(
    sync_celery, global_session, pipeline
):
    """De-identification removes the source, not the clinical record."""

    transcript = _transcribe(pipeline.audio)
    note = _note_for(pipeline.appointment)
    assert note
    note_id = note.id

    anonymize.perform(transcript.id)

    surviving = AIVisitNote.get(id=note_id)
    assert surviving
    assert surviving.note == GENERATED_NOTE


def test_rerunning_transcription_after_deidentification_does_not_resurrect_phi(
    sync_celery, pipeline
):
    """A retried Celery task must not undo the tombstone."""

    transcript = _transcribe(pipeline.audio)
    before = _retained_count()
    anonymize.perform(transcript.id)
    after_first = _retained_count()

    transcribe.perform(pipeline.audio.id)

    retained = AIVisitTranscript.get(id=transcript.id)
    assert retained is not None
    assert retained.transcript_data == []
    assert after_first - before == 1
    assert _retained_count() == after_first


def test_rerunning_deidentification_is_idempotent(
    sync_celery, global_session, pipeline
):
    """The daily sweeper and a Celery retry can both arrive for one transcript."""

    transcript = _transcribe(pipeline.audio)
    before = _retained_count()

    anonymize.perform(transcript.id)
    first_retained = AIVisitTranscript.get(id=transcript.id)
    assert first_retained is not None
    first_deleted_at = first_retained.deleted_at
    after_first = _retained_count()

    anonymize.perform(transcript.id)

    retained = AIVisitTranscript.get(id=transcript.id)
    assert retained is not None
    assert retained.deleted_at == first_deleted_at
    assert after_first - before == 1
    assert _retained_count() == after_first
