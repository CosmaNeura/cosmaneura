from __future__ import annotations

import hashlib
from typing import Annotated, Literal
from uuid import UUID

import orjson
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator

import app.commands.scribe_transcript
from app import log

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import TranscriptEntry
from app.models.scribe_transcript_delivery import LOGICAL_SCRIBE_PRODUCER
from sqlalchemy.exc import SQLAlchemyError

from .dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_scribe_service_request,
)

service_api_app = APIRouter(
    prefix="/service/v1",
    tags=["service"],
    dependencies=[Depends(aglobal_session)],
)


class ScribeTranscriptSegment(BaseModel, extra="forbid"):
    speaker: Literal["Doctor", "Patient", "Other", "Unknown"]
    content: str = Field(min_length=1, max_length=100_000)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def timestamps_are_ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class ScribeTranscriptDelivery(BaseModel, extra="forbid"):
    schema_version: Literal["1.0"]
    encounter_id: TypeIDType
    scribe_session_id: UUID
    duration_seconds: int = Field(ge=0, le=86_400)
    segments: list[ScribeTranscriptSegment] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def segments_are_ordered(self):
        starts = [segment.start_ms for segment in self.segments]
        if starts != sorted(starts):
            raise ValueError("segments must be in nondecreasing time order")
        return self


class ScribeTranscriptDeliveryResponse(BaseModel, extra="forbid"):
    schema_version: Literal["1.0"] = "1.0"
    transcript_id: TypeIDType
    disposition: Literal["created", "replayed"]


def _payload_sha256(data: ScribeTranscriptDelivery) -> str:
    canonical = orjson.dumps(data.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(canonical).hexdigest()


@service_api_app.post(
    "/scribe/transcripts",
    response_model=ScribeTranscriptDeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_scribe_transcript(
    data: ScribeTranscriptDelivery,
    response: Response,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_scribe_service_request)
    ],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
) -> ScribeTranscriptDeliveryResponse:
    try:
        result = app.commands.scribe_transcript.perform(
            encounter_id=data.encounter_id,
            caller_client_id=principal.client_id,
            idempotency_key=idempotency_key,
            scribe_session_id=data.scribe_session_id,
            payload_sha256=_payload_sha256(data),
            duration=data.duration_seconds,
            transcript_data=[
                TranscriptEntry(
                    speaker=segment.speaker,
                    content=segment.content,
                    startTime=segment.start_ms,
                    endTime=segment.end_ms,
                )
                for segment in data.segments
            ],
        )
    except app.commands.scribe_transcript.EncounterNotFoundError as exc:
        log.warning(
            "scribe transcript delivery rejected",
            action="scribe_handoff.delivery_rejected",
            error_class="encounter_not_found",
            actual_caller_client_id=principal.client_id,
            logical_producer=LOGICAL_SCRIBE_PRODUCER,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Encounter not found.",
        ) from exc
    except app.commands.scribe_transcript.DoctorAccessNotInForceError as exc:
        log.warning(
            "scribe transcript delivery rejected",
            action="scribe_handoff.delivery_rejected",
            error_class="doctor_access_not_in_force",
            actual_caller_client_id=principal.client_id,
            logical_producer=LOGICAL_SCRIBE_PRODUCER,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Encounter access is not active.",
        ) from exc
    except app.commands.scribe_transcript.TranscriptConflictError as exc:
        log.warning(
            "scribe transcript delivery rejected",
            action="scribe_handoff.delivery_rejected",
            error_class="delivery_conflict",
            actual_caller_client_id=principal.client_id,
            logical_producer=LOGICAL_SCRIBE_PRODUCER,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except app.commands.scribe_transcript.TranscriptPersistenceError as exc:
        log.warning(
            "scribe transcript delivery rejected",
            action="scribe_handoff.delivery_rejected",
            error_class="persistence_unavailable",
            actual_caller_client_id=principal.client_id,
            logical_producer=LOGICAL_SCRIBE_PRODUCER,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcript delivery is temporarily unavailable.",
        ) from exc
    except SQLAlchemyError as exc:
        log.warning(
            "scribe transcript delivery rejected",
            action="scribe_handoff.delivery_rejected",
            error_class="database_unavailable",
            actual_caller_client_id=principal.client_id,
            logical_producer=LOGICAL_SCRIBE_PRODUCER,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcript delivery is temporarily unavailable.",
        ) from exc

    if result.disposition == "replayed":
        response.status_code = status.HTTP_200_OK

    log.info(
        "scribe transcript delivery accepted",
        action=f"scribe_handoff.delivery_{result.disposition}",
        actual_caller_client_id=principal.client_id,
        logical_producer=LOGICAL_SCRIBE_PRODUCER,
    )

    return ScribeTranscriptDeliveryResponse(
        transcript_id=result.transcript.id,
        disposition=result.disposition,
    )
