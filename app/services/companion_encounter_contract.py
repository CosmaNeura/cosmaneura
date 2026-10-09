"""Closed account/appointment discovery, separate from clinical record contents."""

import hashlib
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from app.services.companion_record_contract import (
    ActorRef,
    AppointmentRef,
    Closed,
    Digest,
    Label,
    PracticeRef,
    RecordAppointment,
    RecordPatient,
    TenantRef,
    TypeSuffix,
    canonical,
)

PAGE_SIZE = 25
MAX_WINDOW_DAYS = 31
MAX_RESPONSE_BYTES = 65_536
ProviderLinkRef = Annotated[
    str, Field(pattern=r"^scribe_provider_link" + TypeSuffix, strict=True)
]


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class ConnectionRequest(Closed):
    action: Literal["connection"] = "connection"
    expected_tenant_ref: TenantRef
    request_nonce: UUID


class ConnectedRequest(Closed):
    expected_tenant_ref: TenantRef
    expected_actor_ref: ActorRef
    connection_sha256: Digest
    request_nonce: UUID


class EncounterWindow(Closed):
    """Core calendar dates: start inclusive, end exclusive, no inferred zone."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def bounded_window(self):
        if not 1 <= (self.end_date - self.start_date).days <= MAX_WINDOW_DAYS:
            raise ValueError("invalid_encounter_window")
        return self


class EncounterCursor(Closed):
    window_sha256: Digest
    connection_sha256: Digest
    appointment_date: datetime
    appointment_ref: AppointmentRef


class EncountersRequest(ConnectedRequest):
    action: Literal["encounters"] = "encounters"
    window: EncounterWindow
    cursor: EncounterCursor | None = None

    @model_validator(mode="after")
    def current_cursor(self):
        if self.cursor is not None and (
            self.cursor.connection_sha256 != self.connection_sha256
            or self.cursor.window_sha256 != digest(self.window.model_dump(mode="json"))
            or not self.window.start_date
            <= self.cursor.appointment_date.date()
            < self.window.end_date
        ):
            raise ValueError("invalid_encounter_cursor")
        return self


class EncounterRequest(ConnectedRequest):
    action: Literal["encounter"] = "encounter"
    appointment_ref: AppointmentRef
    source_sha256: Digest


DiscoveryRequest = Annotated[
    ConnectionRequest | EncountersRequest | EncounterRequest,
    Field(discriminator="action"),
]
REQUEST_ADAPTER = TypeAdapter(DiscoveryRequest)


class ConnectionContent(Closed):
    actor_ref: ActorRef
    tenant_ref: TenantRef
    provider_link_ref: ProviderLinkRef
    practice_ref: PracticeRef
    practice_label: Label = Field(repr=False)
    review_sha256: Digest


class ConnectionSnapshot(ConnectionContent):
    connection_sha256: Digest

    @model_validator(mode="after")
    def exact_connection(self):
        if self.connection_sha256 != digest(
            self.model_dump(mode="json", exclude={"connection_sha256"})
        ):
            raise ValueError("invalid_connection_digest")
        return self


class EncounterContent(Closed):
    connection_sha256: Digest
    patient: RecordPatient = Field(repr=False)
    appointment: RecordAppointment

    @model_validator(mode="after")
    def exact_patient(self):
        if self.patient.patient_ref != self.appointment.patient_ref:
            raise ValueError("invalid_encounter_patient")
        return self


class EncounterSnapshot(EncounterContent):
    source_sha256: Digest

    @model_validator(mode="after")
    def exact_source(self):
        if self.source_sha256 != digest(
            self.model_dump(mode="json", exclude={"source_sha256"})
        ):
            raise ValueError("invalid_encounter_digest")
        return self


class DiscoveryResponse(Closed):
    contract_version: Literal["1"] = "1"
    action: Literal["connection", "encounters", "encounter"]
    request_nonce: UUID
    checked_at: AwareDatetime
    connection: ConnectionSnapshot = Field(repr=False)
    encounters: tuple[EncounterSnapshot, ...] = Field(max_length=PAGE_SIZE, repr=False)
    next_cursor: EncounterCursor | None

    @model_validator(mode="after")
    def closed_result(self):
        refs = [entry.appointment.appointment_ref for entry in self.encounters]
        if (
            len(refs) != len(set(refs))
            or any(
                entry.connection_sha256 != self.connection.connection_sha256
                for entry in self.encounters
            )
            or self.action == "connection"
            and self.encounters
            or self.action == "encounter"
            and len(self.encounters) != 1
            or self.action != "encounters"
            and self.next_cursor is not None
            or self.next_cursor is not None
            and (
                self.next_cursor.connection_sha256 != self.connection.connection_sha256
            )
            or len(self.model_dump_json().encode()) > MAX_RESPONSE_BYTES
        ):
            raise ValueError("invalid_discovery_result")
        return self
