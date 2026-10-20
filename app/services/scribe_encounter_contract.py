"""Closed provider-entered encounter contract. Source references are Core IDs."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_serializer,
)

from app.services.companion_record_contract import (
    ActorRef,
    AppointmentRef,
    Label,
    PatientRef,
    TenantRef,
)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def private_text_utf8(value: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("private_note_text_invalid") from None
    return value


PrivateNoteText = Annotated[
    str,
    Field(max_length=2000, pattern=r"^[^\x00]*$", repr=False),
    AfterValidator(private_text_utf8),
]


class ExistingPatient(Closed):
    kind: Literal["existing"]
    patient_ref: PatientRef


class NewPatient(Closed):
    kind: Literal["new"]
    name: Label
    date_of_birth: date | None = None
    provisional: bool = Field(default=False, strict=True)

    @model_serializer(mode="wrap")
    def compatible_serialization(self, handler):
        value = handler(self)
        if not self.provisional:
            value.pop("provisional", None)
        return value

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


class AddBirthDateRequest(Request):
    action: Literal["add_birth_date"]
    appointment_ref: AppointmentRef
    patient_ref: PatientRef
    change_key: UUID
    date_of_birth: date


class PrivateNoteReadRequest(Request):
    action: Literal["private_note_read"]
    appointment_ref: AppointmentRef


class PrivateNoteSaveRequest(Request):
    action: Literal["private_note_save"]
    appointment_ref: AppointmentRef
    change_key: UUID
    expected_version: int = Field(ge=0, le=2_147_483_647, strict=True)
    text: PrivateNoteText


class PatientMatchCandidatesRequest(Request):
    action: Literal["patient_match_candidates"]
    appointment_ref: AppointmentRef
    query: str = Field(default="", max_length=200, pattern=r"^[^\x00]*$")
    date_of_birth: date | None = None


class ConfirmNewPatient(Closed):
    kind: Literal["confirm_new"]
    date_of_birth: date | None = None


class PatientMatchSaveRequest(Request):
    action: Literal["patient_match_save"]
    appointment_ref: AppointmentRef
    change_key: UUID
    expected_version: int = Field(ge=0, le=2_147_483_647, strict=True)
    selection: Annotated[
        ExistingPatient | ConfirmNewPatient, Field(discriminator="kind")
    ]


EncounterRequest = (
    PatientsRequest
    | CatalogRequest
    | LookupRequest
    | CreateRequest
    | AddBirthDateRequest
    | PrivateNoteReadRequest
    | PrivateNoteSaveRequest
    | PatientMatchCandidatesRequest
    | PatientMatchSaveRequest
)
REQUEST_ADAPTER: TypeAdapter[EncounterRequest] = TypeAdapter(
    Annotated[EncounterRequest, Field(discriminator="action")]
)
