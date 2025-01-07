import json
import os
import uuid
from textwrap import dedent

import assemblyai as assembly
from azure.storage.blob import BlobServiceClient
from decouple import config
from openai import OpenAI
from pydub import AudioSegment

from app import log
from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_CONTAINER_NAME,
)

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.visit_audio_file import VisitAudioFile


class TranscriptionResult:
    def __init__(self, transcript: list[TranscriptEntry], duration: int):
        self.transcript = transcript
        self.duration = duration


assembly_key = config("ASSEMBLY_API_KEY", cast=str)
if not assembly_key:
    raise ValueError(
        "API key not found. Please set ASSEMBLY_API_KEY in your .env file."
    )
assembly.settings.api_key = assembly_key

api_key = config("OPENAI_API_KEY", cast=str)
if not api_key:
    raise ValueError("API key not found. Please set OPENAI_API_KEY in your .env file.")
openai = OpenAI(api_key=api_key)


def transcribe(visit_audio_file_id: str):
    visit_audio_file = VisitAudioFile.get(id=visit_audio_file_id)
    assert visit_audio_file

    result = transcribe_audio(visit_audio_file)

    AIVisitTranscript.find_or_create_by(
        doctor_id=visit_audio_file.doctor_id,
        appointment_id=visit_audio_file.appointment_id,
        patient_id=visit_audio_file.patient_id,
        duration=result.duration,
        transcript_data=[item.model_dump() for item in result.transcript],
    )


def extract_audio_to_temp(visit: VisitAudioFile) -> str:
    filename = visit.azure_file_name

    temp_download_path = f"./tmp/visit-audio-{uuid.uuid4()}-{filename}"
    basename_no_ext = os.path.splitext(filename)[0]
    temp_audio_path = f"/tmp/{uuid.uuid4()}-{basename_no_ext}.wav"

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )
    blob_client = service_client.get_blob_client(
        container=AZURE_UPLOADS_CONTAINER_NAME,
        blob=filename,
    )

    try:
        with open(temp_download_path, "wb") as file:
            file.write(blob_client.download_blob().readall())

        extract_audio_from_mp4(temp_download_path, output_audio_path=temp_audio_path)

        return temp_audio_path

    finally:
        if os.path.exists(temp_download_path):
            os.remove(temp_download_path)


def extract_audio_from_mp4(mp4_path, output_audio_path="extracted_audio.wav"):
    audio = AudioSegment.from_file(mp4_path, format="mp4")
    audio.export(output_audio_path, format="wav")


# Function to transcribe and concatenate results
def transcribe_audio(visit_audio_file: VisitAudioFile) -> TranscriptionResult:
    if visit_audio_file.azure_file_name.lower().endswith(".mp4"):
        audio_file = extract_audio_to_temp(visit_audio_file)
    # elif visit_audio_file.azure_file_name.lower().endswith(".wav"):
    #     download the audio file from blob to temp storage
    else:
        ext = os.path.splitext(visit_audio_file.azure_file_name)[1]
        raise ValueError(
            f"Unsupported file format: .{ext}. Please provide a .mp4 or .wav file."
        )

    try:
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
        if os.path.exists(audio_file):
            os.remove(audio_file)

    log.info("identifying speakers", audio_file=audio_file)
    sample = "\n".join([f"{u.speaker}: {u.text}" for u in transcript.utterances[:10]])
    classification = identify_doctor_from_transcript(sample)
    doctor_label = extract_doctor_classification(classification)
    if doctor_label is None:
        log.error(
            "speaker identifiation failed",
            audio_file=audio_file,
            classification=classification,
        )
        raise ValueError("Could not extract doctor classification from reasoning.")

    structured_transcript = []

    # Utterance(
    #   text='Okay, Go now.',
    #   start=1360,
    #   end=2792,
    #   confidence=0.98156,
    #   speaker='A',
    #   channel=None,
    #   words=[
    #     UtteranceWord(text='Okay,', start=1360, end=1840, confidence=0.94812, speaker='A', channel=None),
    #     UtteranceWord(text='Go', start=1880, end=2120, confidence=0.99861, speaker='A', channel=None),
    for utterance in transcript.utterances:
        label = utterance.speaker
        label = "Doctor" if doctor_label == utterance.speaker else "Patient"
        entry = TranscriptEntry(
            speaker=label,
            content=utterance.text,
            startTime=utterance.start,
            endTime=utterance.end,
        )
        structured_transcript.append(entry)

    return TranscriptionResult(
        transcript=structured_transcript,
        duration=transcript.audio_duration or 0,
    )


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
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": prompt},
        ],
    )

    reasoning = response.choices[0].message.content
    return reasoning if reasoning is not None else None


def extract_doctor_classification(reasoning: str | None) -> str | None:
    if reasoning is None:
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
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    # Parse and return the value of "doctor"
    response_content = response.choices[0].message.content
    if response_content is None:
        return None
    return json.loads(response_content)["doctor"]
