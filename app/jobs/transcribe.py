"""

This is the first step in the visit upload process

"""

import json
import os
import subprocess
import uuid
from textwrap import dedent
from typing import Any, cast

import assemblyai as assembly
import sentry_sdk
from azure.storage.blob import BlobServiceClient

import app.jobs.extract_medications
import app.jobs.score_engagement
import app.jobs.summarize
from app import log
from app.celery import celery_app
from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_CONTAINER_NAME,
)
from app.configuration.openai import DEFAULT_MULTI_MODAL_MODEL, openai

from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.visit_audio_file import VisitAudioFile

# https://support.assemblyai.com/articles/2616970375-what-audio-and-video-file-types-are-supported-by-your-api
SUPPORTED_AUDIO_FILE_TYPES = ["wav", "mp3", "m4a", "flac", "ogg", "aac"]
SUPPORTED_VIDEO_FILE_TYPES = ["mp4", "m4a", "mov", "avi"]


class TranscriptionResult:
    def __init__(self, transcript: list[TranscriptEntry], duration: int):
        self.transcript = transcript
        self.duration = duration


def perform(visit_audio_file_id: str | TypeIDType):
    """
    Perform transcription via assembly of visit audio or video file.
    """

    log.info("starting transcription job", visit_audio_file_id=visit_audio_file_id)
    visit_audio_file = VisitAudioFile.get(id=visit_audio_file_id)
    assert visit_audio_file

    # TODO extract out to helper
    sentry_sdk.set_user({"id": visit_audio_file.doctor_id})
    log.local(doctor_id=visit_audio_file.doctor_id)

    existing_transcript = AIVisitTranscript.get(
        doctor_id=visit_audio_file.doctor_id,
        appointment_id=visit_audio_file.appointment_id,
        patient_id=visit_audio_file.patient_id,
    )

    if existing_transcript is not None:
        log.info("transcript already exists, skipping job")
        return

    result = transcribe_audio(visit_audio_file)

    log.info("creating transcript record", duration=result.duration)
    transcript = AIVisitTranscript.find_or_create_by(
        doctor_id=visit_audio_file.doctor_id,
        appointment_id=visit_audio_file.appointment_id,
        patient_id=visit_audio_file.patient_id,
        duration=result.duration,
        # TODO I *think* this was fixed in a recent AM update, should check later
        transcript_data=[item.model_dump() for item in result.transcript],
    )

    queue_schema_transcription(transcript)

    # Engagement engine v2 (staging): score the patient's enacted communication
    # role from this transcript. Independent of note schemas, so enqueue once here.
    app.jobs.score_engagement.queue(transcript_id=transcript.id)

    # Extract medications/supplements into structured, provider-confirmable rows.
    app.jobs.extract_medications.queue(transcript_id=transcript.id)


perform_celery = celery_app.task(perform)


# TODO we lose the typing with this approach... is there a better way?
def queue(*args, **kwargs):
    log.info("queuing transcription", args=args, kwargs=kwargs)
    perform_celery.delay(*args, **kwargs)


def queue_schema_transcription(transcript: AIVisitTranscript):
    # now that the transcript exists, let's summarize it for each note type the doctor has
    # Get all note schemas for the doctor and issue summary jobs
    note_schemas = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == transcript.doctor_id)
        & cast(Any, DoctorNoteSchema.deleted_at).is_(None)
    ).all()

    if not note_schemas:
        log.warning("no note schemas found for doctor", doctor_id=transcript.doctor_id)
        return

    for schema in cast(list[DoctorNoteSchema], note_schemas):
        app.jobs.summarize.queue(transcript_id=transcript.id, schema_id=schema.id)


def download_blob(visit: VisitAudioFile) -> str:
    filename = visit.azure_file_name

    temp_download_path = f"./tmp/visit-audio-{uuid.uuid4()}-{filename}"

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )

    blob_client = service_client.get_blob_client(
        container=AZURE_UPLOADS_CONTAINER_NAME,
        blob=filename,
    )

    log.info("blob download starting", temp_path=temp_download_path)

    # TODO Set a timeout, this seems to hang.
    download_stream = blob_client.download_blob(max_concurrency=8)

    with open(temp_download_path, "wb") as file:
        for chunk in download_stream.chunks():
            file.write(chunk)

    log.info("blob download complete", temp_path=temp_download_path)

    return temp_download_path


def extract_audio_to_temp(visit: VisitAudioFile, format: str) -> str:
    filename = visit.azure_file_name
    basename_no_ext = os.path.splitext(filename)[0]
    temp_audio_path = f"/tmp/{uuid.uuid4()}-{basename_no_ext}.wav"
    temp_download_path = ""

    try:
        temp_download_path = download_blob(visit)

        log.info("extracting audio from video", output_path=temp_audio_path)
        extract_audio_from_video(
            video_path=temp_download_path,
            format=format,
            output_audio_path=temp_audio_path,
        )

        return temp_audio_path

    finally:
        if temp_download_path and os.path.exists(temp_download_path):
            log.info("cleaning up temp download file", path=temp_download_path)
            os.remove(temp_download_path)


def extract_audio_from_video(
    video_path, format, output_audio_path="extracted_audio.wav"
):
    subprocess.run(
        [
            "ffmpeg",
            "-i",
            video_path,  # Input video file
            "-vn",  # Disable video
            "-acodec",
            "pcm_s16le",  # Use WAV-compatible codec
            "-f",
            "wav",  # Output format
            output_audio_path,  # Output file
        ],
        check=True,
    )


# Function to transcribe and concatenate results
def transcribe_audio(visit_audio_file: VisitAudioFile) -> TranscriptionResult:
    log.info("starting audio transcription", file_name=visit_audio_file.azure_file_name)
    audio_file = ""

    try:
        ext = os.path.splitext(visit_audio_file.azure_file_name.lower())[1][1:]
        if ext in SUPPORTED_AUDIO_FILE_TYPES:
            audio_file = download_blob(visit_audio_file)
        elif ext in SUPPORTED_VIDEO_FILE_TYPES:
            audio_file = extract_audio_to_temp(visit_audio_file, format=ext)
        else:
            log.error("unsupported file format", extension=ext)
            raise ValueError(f"Unsupported file format: .{ext}")

        transcriber = assembly.Transcriber()
        config = assembly.TranscriptionConfig(speaker_labels=True, speakers_expected=2)

        log.info("transcribing audio", audio_file=audio_file)
        transcript = transcriber.transcribe(audio_file, config)
    except Exception as e:
        log.error("transcription failed", audio_file=audio_file, error=e)
        raise ValueError(f"Transcription failed: {e}")
    else:
        if (
            transcript.status == assembly.TranscriptStatus.error
            or transcript.utterances is None
        ):
            log.error(
                "transcription failed", audio_file=audio_file, error=transcript.error
            )
            raise ValueError(f"Transcription failed: {transcript.error}")
    finally:
        if audio_file and os.path.exists(audio_file):
            log.info("cleaning up audio file", path=audio_file)
            os.remove(audio_file)

    # AssemblyAI labels speakers "A"/"B"; map those onto Doctor/Patient. Shared
    # with the Zoom/Recall path (recall_webhooks.py), which arrives with Zoom
    # participant names instead — label_speakers normalizes both.
    log.info("structuring transcript data")
    structured_transcript = label_speakers(
        [
            TranscriptEntry(
                speaker=utterance.speaker or "Unknown",
                content=utterance.text,
                startTime=utterance.start,
                endTime=utterance.end,
            )
            for utterance in transcript.utterances
        ]
    )

    log.info("transcription complete", duration=transcript.audio_duration)
    return TranscriptionResult(
        transcript=structured_transcript,
        duration=transcript.audio_duration or 0,
    )


def label_speakers(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """Relabel each entry's ``speaker`` to ``"Doctor"`` or ``"Patient"``.

    The raw speaker labels differ by source: AssemblyAI (audio upload) uses
    ``"A"``/``"B"``, while Recall (Zoom bot) uses the participant display name.
    Downstream consumers -- note generation and, critically,
    ``reconstruct_patient_text`` for engagement scoring -- only recognize the
    literal ``"Patient"`` label, so both paths must normalize here.

    The doctor-identification prompt is hardcoded to two speakers ``A`` and
    ``B``, so we map the distinct raw labels onto A/B for the LLM, then map its
    answer back to the raw label. The round-trip is fully reversible, so the
    final Doctor/Patient assignment is independent of the A/B mapping direction
    (the audio-upload path, already A/B, is unaffected).
    """
    if not entries:
        return entries

    # Distinct raw labels in order of first appearance; a visit has two speakers.
    raw_labels: list[str] = []
    for entry in entries:
        if entry.speaker not in raw_labels:
            raw_labels.append(entry.speaker)

    synthetic_by_raw = {raw: chr(ord("A") + i) for i, raw in enumerate(raw_labels)}
    raw_by_synthetic = {syn: raw for raw, syn in synthetic_by_raw.items()}

    log.info("identifying speakers", raw_labels=raw_labels)
    sample = "\n".join(
        f"{synthetic_by_raw[e.speaker]}: {e.content}" for e in entries[:10]
    )
    classification = identify_doctor_from_transcript(sample)
    doctor_synthetic = extract_doctor_classification(classification)
    if doctor_synthetic is None:
        log.error("speaker identification failed", classification=classification)
        raise ValueError("Could not extract doctor classification from reasoning.")

    doctor_raw = raw_by_synthetic.get(doctor_synthetic)
    return [
        TranscriptEntry(
            speaker="Doctor" if entry.speaker == doctor_raw else "Patient",
            content=entry.content,
            startTime=entry.startTime,
            endTime=entry.endTime,
        )
        for entry in entries
    ]


def identify_doctor_from_transcript(transcript: str) -> str | None:
    prompt = dedent(f"""
        I'm going to give you a transcript from a clinical encounter between a
        patient and a doctor. The transcript will have two speakers in it labeled A
        and B. One is the doctor and one is the patient. I want you to tell me which
        one you think the doctor is.

        <transcript>
        {transcript}
        </transcript>

        Take your time and think step by step. Which speaker label do you think
        corresponds with the doctor? What evidence do you have to support this
        theory? What are some quotes from the transcript that back up your decision?
    """)

    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": prompt},
        ],
    )

    reasoning = response.choices[0].message.content
    return reasoning if reasoning is not None else None


def extract_doctor_classification(reasoning: str | None) -> str | None:
    if reasoning is None:
        log.error("no reasoning provided for doctor classification")
        return None

    # TODO Use structured outputs instead of json mode. This allows for definition of
    # response schema with pydantic and better refusal handling.
    # https://platform.openai.com/docs/guides/structured-outputs
    prompt = dedent(f"""
        I'm going to give you the output of another LLM. The LLM was given a
        transcript of a clinical encounter between a patient and a doctor with
        the speakers labeled as A and B. The LLM was asked to identify
        which speaker (A or B) was the doctor. Here is its reasoning:

        <reasoning>
        {reasoning}
        </reasoning>

        Your job is to extract the LLM's classification from the reasoning above.
        Respond only in JSON format with the following schema:

        {{
          doctor: "A" | "B"
        }}
    """)

    response = openai.chat.completions.create(
        model=DEFAULT_MULTI_MODAL_MODEL,
        messages=[
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    # Parse and return the value of "doctor"
    response_content = response.choices[0].message.content
    if response_content is None:
        log.error("no response content from OpenAI")
        return None

    return json.loads(response_content)["doctor"]
