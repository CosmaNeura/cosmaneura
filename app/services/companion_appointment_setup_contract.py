"""Reviewed, bounded source-reference setup; no source clinical content."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.services.companion_encounter_contract import (
    PAGE_SIZE,
    Closed,
    ConnectedRequest,
    ConnectionSnapshot,
    Digest,
    EncounterCursor,
    EncounterWindow,
    digest,
)

MAX_RESPONSE_BYTES = 4096


class AppointmentSetupRequest(ConnectedRequest):
    action: Literal["preview", "connect"]
    window: EncounterWindow
    cursor: EncounterCursor | None = None
    plan_sha256: Digest | None = None

    @model_validator(mode="after")
    def exact_plan(self):
        if (self.action == "connect") != (self.plan_sha256 is not None):
            raise ValueError("appointment_setup_plan_required")
        if self.cursor is not None and (
            self.cursor.connection_sha256 != self.connection_sha256
            or self.cursor.window_sha256 != digest(self.window.model_dump(mode="json"))
            or self.cursor.appointment_date.utcoffset() is not None
            or not self.window.start_date <= self.cursor.appointment_date.date() < self.window.end_date
        ):
            raise ValueError("invalid_appointment_setup_cursor")
        return self


class AppointmentSetupResponse(Closed):
    contract_version: Literal["1"] = "1"
    action: Literal["preview", "connect"]
    request_nonce: UUID
    checked_at: AwareDatetime
    connection: ConnectionSnapshot = Field(repr=False)
    window: EncounterWindow
    plan_sha256: Digest
    scanned_count: int = Field(ge=0, le=PAGE_SIZE, strict=True)
    eligible_count: int = Field(ge=0, le=PAGE_SIZE, strict=True)
    already_connected_count: int = Field(ge=0, le=PAGE_SIZE, strict=True)
    review_required_count: int = Field(ge=0, le=PAGE_SIZE, strict=True)
    connected_now_count: int = Field(ge=0, le=PAGE_SIZE, strict=True)
    next_cursor: EncounterCursor | None

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.scanned_count != self.eligible_count + self.already_connected_count + self.review_required_count:
            raise ValueError("invalid_appointment_setup_counts")
        if self.connected_now_count != (self.eligible_count if self.action == "connect" else 0):
            raise ValueError("invalid_appointment_setup_outcome")
        if self.next_cursor is not None and (
            self.scanned_count != PAGE_SIZE
            or self.next_cursor.connection_sha256 != self.connection.connection_sha256
            or self.next_cursor.window_sha256 != digest(self.window.model_dump(mode="json"))
            or self.next_cursor.appointment_date.utcoffset() is not None
            or not self.window.start_date <= self.next_cursor.appointment_date.date() < self.window.end_date
        ):
            raise ValueError("invalid_appointment_setup_page")
        if len(self.model_dump_json().encode()) > MAX_RESPONSE_BYTES:
            raise ValueError("appointment_setup_capacity")
        return self
