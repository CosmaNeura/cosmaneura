"""Closed provider-entered encounter contract. Source references are Core IDs."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
)

from app.services.companion_record_contract import (
    ActorRef,
    Label,
    PatientRef,
    TenantRef,
)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ExistingPatient(Closed):
    kind: Literal["existing"]
    patient_ref: PatientRef


class NewPatient(Closed):
    kind: Literal["new"]
    name: Label
    date_of_birth: date | None = None

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value):
        if value != value.strip() or not value:
            raise ValueError("patient_name_required")
        return value


class Request(Closed):
    expected_actor_ref: ActorRef
    expected_tenant_ref: TenantRef
    request_nonce: UUID


class PatientsRequest(Request):
    action: Literal["patients"]
    query: str = Field(default="", max_length=200, pattern=r"^[^\x00]*$")


class CatalogRequest(Request):
    action: Literal["list"]


class LookupRequest(Request):
    action: Literal["lookup"]
    workspace_id: UUID


class CreateRequest(Request):
    action: Literal["create"]
    creation_key: UUID
    patient: Annotated[ExistingPatient | NewPatient, Field(discriminator="kind")]
    appointment_at: AwareDatetime
    title: Label = "Visit"


EncounterRequest = PatientsRequest | CatalogRequest | LookupRequest | CreateRequest
REQUEST_ADAPTER: TypeAdapter[EncounterRequest] = TypeAdapter(
    Annotated[EncounterRequest, Field(discriminator="action")]
)
