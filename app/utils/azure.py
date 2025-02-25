import json
import os
import uuid
from textwrap import dedent

import assemblyai as assembly
import sentry_sdk
from azure.storage.blob import BlobServiceClient
from pydub import AudioSegment

import app.jobs.summarize
from app import log
from app.celery import celery
from app.configuration.azure import (
    AZURE_UPLOADS_ACCOUNT_KEY,
    AZURE_UPLOADS_ACCOUNT_URL,
    AZURE_UPLOADS_CONTAINER_NAME,
)
from app.configuration.openai import openai

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.visit_audio_file import VisitAudioFile


def download_blob(filename: str, container_name: str):
    # TODO should we restrict container_name to valid entries like `AZURE_UPLOADS_CONTAINER_NAME`

    service_client = BlobServiceClient(
        AZURE_UPLOADS_ACCOUNT_URL,
        credential=AZURE_UPLOADS_ACCOUNT_KEY,
    )

    blob_client = service_client.get_blob_client(
        container=container_name,
        blob=filename,
    )

    return blob_client.download_blob().readall()
