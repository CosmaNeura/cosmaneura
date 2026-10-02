"""Closed source preparation contract; no CAB permission or model authority."""

import hashlib
import json
import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Ref = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
]
PROJECTION = "ordered-note-markdown.v1"
# Exactly the standalone paragraph emitted by legacy augment_clinical_note.
_RECORDING_TIME = re.compile(
    r"(?m)^Greater than [0-9]+ minutes was spent in (?:patient|client) care, "
    r"including review of prior records, consultation with other providers, and documentation\.\r?$"
)


class Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


def project_note(value: object) -> tuple[str, str, str, bool]:
    if not isinstance(value, dict) or not 1 <= len(value) <= 64:
        raise ValueError("billing_source_invalid")
    sections, rendered, excluded = [], [], False
    for title, body in value.items():
        if (
            not isinstance(title, str)
            or not 1 <= len(title) <= 200
            or any(ord(c) < 32 or ord(c) == 127 for c in title)
            or not isinstance(body, str)
            or any((ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127 for c in body)
            or "\r" in body.replace("\r\n", "")
        ):
            raise ValueError("billing_source_invalid")
        sections.append([title, body])
        # A complete paragraph requires blank boundaries, not an inline mention.
        pieces = re.split(r"((?:\r?\n){2,})", body)
        kept = []
        for index, piece in enumerate(pieces):
            if index % 2 == 0 and _RECORDING_TIME.fullmatch(piece.strip("\r\n")):
                excluded = True
                continue
            kept.append(piece)
        rendered.append("## " + title + "\n" + "".join(kept))
    original = json.dumps(sections, ensure_ascii=False, separators=(",", ":"))
    projected = "\n\n".join(rendered)
    try:
        if (
            len(original.encode()) > 262144
            or len(projected) > 131072
            or not any(body.strip() for _, body in sections)
        ):
            raise ValueError
        original_sha = hashlib.sha256(original.encode()).hexdigest()
        projected.encode()
    except (ValueError, UnicodeEncodeError):
        raise ValueError("billing_source_invalid") from None
    return original, projected, original_sha, excluded


class BillingSource(Closed):
    noteId: Ref
    appointmentId: Ref
    noteSchemaId: Ref
    contentSha256: Digest
    textSha256: Digest
    projectionVersion: Literal[PROJECTION] = PROJECTION
    excludedRecordingTime: bool


class ConfirmedBillingVisit(Closed):
    patientAge: int = Field(ge=0, le=130)
    patientSex: Literal["M", "F", "other", "unknown"]
    encounterType: Literal[
        "office_new", "office_established", "telehealth_new", "telehealth_established"
    ]
    placeOfService: Literal["11", "02", "10"]
    providerSpecialty: str = Field(
        min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$"
    )
    serviceDate: str | None = None

    @field_validator("serviceDate", mode="before")
    @classmethod
    def calendar_day(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("invalid_service_date")
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def consistent(self):
        if not self.providerSpecialty.strip() or (
            self.encounterType.startswith("office_") != (self.placeOfService == "11")
        ):
            raise ValueError("invalid_visit_context")
        self.providerSpecialty.encode("utf-8")
        return self


class PrepareBillingHeaderOperation(Closed):
    operationId: str
    expectedContentSha256: Digest
    expectedTextSha256: Digest
    confirmedComplete: Literal[True]
    visit: ConfirmedBillingVisit

    @field_validator("operationId")
    @classmethod
    def operation_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("invalid_operation_id")
        return value

    @field_validator("confirmedComplete", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("completion_confirmation_required")
        return value


class PreparedSummary(Closed):
    operationId: str
    sourceRevision: int
    state: Literal["prepared"] = "prepared"
    createdAt: datetime


class BillingVisitSuggestions(Closed):
    serviceDate: None = None
    patientAge: None = None
    patientSex: None = None
    encounterType: None = None
    placeOfService: None = None
    providerSpecialty: None = None


class BillingHeaderSource(Closed):
    schemaVersion: Literal["core.billing-header-source.v1"] = (
        "core.billing-header-source.v1"
    )
    state: Literal["not_configured", "review_required"]
    source: BillingSource
    visit: BillingVisitSuggestions = Field(default_factory=BillingVisitSuggestions)
    latestOperation: PreparedSummary | None = None


class BillingTransportState(Closed):
    state: Literal["not_configured"] = "not_configured"


class BillingHeaderOperation(Closed):
    schemaVersion: Literal["core.billing-header-operation.v1"] = (
        "core.billing-header-operation.v1"
    )
    operationId: str
    state: Literal["prepared"] = "prepared"
    sourceRevision: int
    source: BillingSource
    completedAt: datetime
    transport: BillingTransportState = Field(default_factory=BillingTransportState)
    replayed: bool
