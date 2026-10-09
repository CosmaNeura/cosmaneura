"""Configured capture admission v1; preferences are separate from creator authority."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.services.scribe_capture_working_contract import ProtectedContract


class ConfiguredCaptureSelection(ProtectedContract):
    schema_version: Literal["1.0"]
    language_mode: Literal["en-US", "en-US+es-US"]


class ConfiguredCaptureReservation(ConfiguredCaptureSelection):
    id: UUID
    creator_actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ConfiguredSessionCreate(ConfiguredCaptureSelection):
    id: UUID
    appointment_id: str = Field(min_length=1, max_length=128)
    practitioner_id: str = Field(min_length=1, max_length=128)
    patient_id: str = Field(min_length=1, max_length=128)
    consent_record_id: UUID
    modality: Literal["in_person", "virtual", "phone"]


class ConfiguredSessionReceipt(ConfiguredSessionCreate):
    creator_actor_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    ingest_mode: Literal["chunk_upload"]
    transcript_contract_version: Literal["1.0"]
    transcription_adapter_id: Literal["azure_ai_speech"]
    transcription_model_profile_id: Literal["azure_fast_transcription_en_es_v1"]
    normalization_profile_id: Literal["azure_phrase_en_es_evidence_v1"]
    status: Literal["admitted"]
    created_at: AwareDatetime
