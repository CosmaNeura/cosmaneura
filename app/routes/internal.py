from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field, computed_field, model_validator

import app.commands.uploads.sas
import app.commands.uploads.upload_visit_audio_file
import app.jobs.extract
import app.jobs.extract_labs as extract_labs_job
import app.jobs.extract_medications as extract_medications_job
import app.jobs.fetch_drug_reference as fetch_drug_reference_job
import app.jobs.sync_zoom_meetings as sync_zoom_meetings
import app.services.ehr_sandbox_record_mapping as ehr_sandbox_record_mapping
import app.services.zoom as zoom_service
from app.commands.uploads.sas import UploadType
from app.configuration.redis import get_redis
from app.services import lab_ranges as lab_ranges_service
from app.services.biomarkers import (
    BY_KEY as BIOMARKERS_BY_KEY,
)
from app.services.biomarkers import (
    default_unit,
    normalize_biomarker,
)
from app.services.biomarkers import (
    display_name as biomarker_display_name,
)
from app.services.nutrient_depletion import DISCLAIMER as DEPLETION_DISCLAIMER
from app.services.nutrient_depletion import depletions_for
from app.services.reference_links import (
    PROVIDER_RESOURCE_DIRECTORY,
    ResourceLink,
    resource_links_for,
)
from app.services.reference_toolkit_gate import reference_toolkit_enabled_for
from app.services.sovereign_record_grant import (
    RECORD_GRANT_PATTERN,
    RecordGrantConfigurationError,
    configured_record_grant_key,
)
from app.services.zoom import get_zoom_integration, zoom_revoked_redis_key

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor, Terminology
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.drug_reference import DrugReference
from app.models.engagement_role import EngagementRole
from app.models.intake.intake import Intake
from app.models.lab_panel import LabPanel, LabPanelSource, LabPanelStatus
from app.models.lab_range_set import LabRangeSet
from app.models.lab_result import LabResult
from app.models.note_type_customization import (
    NoteTypeCustomization,
    PlanOrdering,
    PronounStyle,
    SubjectiveSource,
    default_customization,
)
from app.models.patient import Patient
from app.models.patient_medication import (
    MedicationKind,
    MedicationSource,
    MedicationStatus,
    PatientMedication,
)
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from sqlalchemy.orm import selectinload

from ..configuration.clerk import CLERK_PRIVATE_KEY
from .admin import admin_api_app
from .dependencies.clerk import AuthenticateClerkRequest
from .dependencies.doctor import inject_doctor_record
from .dependencies.login_as import login_as

# extract into variable for test import to easily override dependencies
authenticate_clerk_request_middleware = AuthenticateClerkRequest(CLERK_PRIVATE_KEY)

internal_api_app = APIRouter(
    prefix="/internal/v1",
    # TODO unclear what the tags are used for...
    tags=["private"],
    # think of dependencies as middleware
    dependencies=[
        # NOTE this line could not be more important, look at the underlying implementation!
        Depends(aglobal_session),
        # make sure the user is auth'd via clerk to this endpoint
        Depends(authenticate_clerk_request_middleware),
        # inject a doctor record into the request state
        Depends(inject_doctor_record),
        # allow admins to switch to another doctor
        Depends(login_as),
    ],
)

internal_api_app.include_router(admin_api_app)


class IntakeList(BaseModel, extra="forbid"):
    appointments: list[Appointment]


@internal_api_app.get("/intake")
def intake_list(request: Request) -> IntakeList:
    doctor: Doctor = request.state.doctor

    # get a list of appointments which have an intake object associated with them
    appointment_list = (
        Appointment.where(Appointment.doctor_id == doctor.id)
        # inner join filters out appointments without intake
        .join(Intake)
        # eager-load intake + patient: Appointment serializes intake_status
        # (touches self.intake) and patient_name (touches self.patient) as
        # computed fields, which lazy-fires one SELECT per row without this.
        .options(
            selectinload(cast(Any, Appointment.intake)),
            selectinload(cast(Any, Appointment.patient)),
        )
        .order_by(cast(Any, Appointment.appointment_date).asc())
        .all()
    )

    return IntakeList(appointments=cast(list[Appointment], list(appointment_list)))


class NotetakingData(BaseModel, extra="forbid"):
    appointments: list[Appointment]


@internal_api_app.get("/notes")
def note_list(request: Request) -> NotetakingData:
    """
    A separate endpoint is used to pull data for an individual note, so we just need the appointment ID
    at this point.
    """

    doctor: Doctor = request.state.doctor
    appointment_list = (
        Appointment.where(Appointment.doctor_id == doctor.id)
        # eager-load intake + patient: Appointment serializes intake_status
        # (touches self.intake) and patient_name (touches self.patient) as
        # computed fields, which lazy-fires one SELECT per row without this.
        .options(
            selectinload(cast(Any, Appointment.intake)),
            selectinload(cast(Any, Appointment.patient)),
        )
        .order_by(cast(Any, Appointment.appointment_date).desc())
        .all()
    )

    return NotetakingData(appointments=cast(list[Appointment], list(appointment_list)))


# TODO what about doctors who have an insane patient list?
@internal_api_app.get("/patients")
def patient_list(request: Request) -> list[Patient]:
    "allows users to select an existing patient name and DOB when uploading a note"

    doctor: Doctor = request.state.doctor

    # TODO(mbianco) AM types should be fixed and the types should now display properly here...
    patient_list = (
        Patient.where(Patient.doctor_id == doctor.id)
        .order_by(cast(Any, Patient.name).asc())
        .all()
    )

    return cast(list[Patient], list(patient_list))


class NoteDetail(BaseModel, extra="forbid"):
    appointment: Appointment
    transcript: AIVisitTranscript | None
    notes: list[AIVisitNote] | None
    engagement_role: EngagementRole | None = None

    @computed_field
    @property
    def patient(self) -> Patient:
        return self.appointment.patient


@internal_api_app.get("/notes/{appointment_id}")
def note_detail(
    request: Request,
    appointment_id: Annotated[TypeIDType, Path()],
) -> NoteDetail:
    # TODO error handling for bad IDs
    doctor: Doctor = request.state.doctor
    appointment = cast(
        Appointment,
        Appointment.where(
            Appointment.doctor_id == doctor.id, Appointment.id == appointment_id
        ).one(),
    )

    visit_notes = cast(
        list[AIVisitNote],
        AIVisitNote.where(
            AIVisitNote.appointment_id == appointment_id,
        ).all(),
    )

    # There should only ever be one transcript per appointment. If this
    # invariant is violated, this endpoint will start to cause issues.
    transcript = AIVisitTranscript.get(appointment_id=appointment_id)

    # Engagement engine v2: the patient's enacted communication role, if scored.
    engagement_role = EngagementRole.get(appointment_id=appointment_id)

    note_detail = NoteDetail(
        appointment=appointment,
        transcript=transcript,
        # TODO should be able to handle a generator, right?
        notes=visit_notes,
        engagement_role=engagement_role,
    )

    return note_detail


class OpenSandboxRecord(BaseModel, extra="forbid"):
    """Opaque, short-lived record grant; never a vendor patient identifier."""

    record_ref: str = Field(
        min_length=80,
        max_length=4096,
        pattern=RECORD_GRANT_PATTERN,
        description=(
            "Opaque, short-lived, authenticated grant bound to the Clerk actor "
            "and current doctor/appointment/patient scope. It is not an EHR "
            "patient identifier and clients must not parse it."
        ),
    )


@internal_api_app.get(
    "/notes/{appointment_id}/ehr-labs-record",
    response_model=OpenSandboxRecord,
)
def open_sandbox_labs_record(
    request: Request,
    response: Response,
    appointment_id: Annotated[TypeIDType, Path()],
) -> OpenSandboxRecord:
    """Resolve the authenticated doctor's current synthetic Preview record.

    The browser supplies only its open platform appointment. The opaque EHR
    reference comes exclusively from the operator-provisioned server mapping;
    names and chart content are neither accepted nor returned.
    """

    doctor: Doctor = request.state.doctor
    actor_ref = request.state.auth_state.payload.get("sub")
    if not isinstance(actor_ref, str) or not actor_ref.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not Authenticated",
        )
    # Admin login-as changes request.state.doctor after Clerk authentication.
    # That delegation is useful for ordinary support workflows but is not a
    # ratified EHR authorization path. Never mint a target doctor's record
    # grant for a different Clerk subject.
    if doctor.clerk_id != actor_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Synthetic sandbox record is unavailable.",
        )
    try:
        grant_key = configured_record_grant_key()
    except RecordGrantConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Synthetic sandbox record grants are unavailable.",
        ) from None
    record_ref = ehr_sandbox_record_mapping.issue_open_athena_preview_record_grant(
        doctor_id=doctor.id,
        appointment_id=appointment_id,
        actor_ref=actor_ref,
        key=grant_key,
    )
    if record_ref is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Synthetic sandbox record is unavailable.",
        )
    response.headers["Cache-Control"] = "no-store"
    return OpenSandboxRecord(record_ref=record_ref)


class IntakeDetail(BaseModel, extra="forbid"):
    appointment: Appointment
    intake: Intake
    patient: Patient


@internal_api_app.get("/intake/{appointment_id}")
def intake_detail(
    request: Request,
    appointment_id: Annotated[TypeIDType, Path()],
) -> IntakeDetail:
    # TODO error handling for bad IDs
    doctor: Doctor = request.state.doctor

    appointment = cast(
        Appointment,
        Appointment.where(
            Appointment.doctor_id == doctor.id, Appointment.id == appointment_id
        ).one(),
    )

    assert appointment

    intake = cast(Intake, appointment.intake)

    intake_detail = IntakeDetail(
        appointment=appointment,
        intake=intake,
        patient=appointment.patient,
    )

    return intake_detail


# unique endpoints are used to generate the signed URLs, this is mostly an arbitrary choice. It probably adds an extra
# added level of security and ability to evolve the upload flow in the future without modifying the frontend.
@internal_api_app.post("/visit-audio-file/get-signed-url", response_model=str)
def generate_signed_visit_recording_upload_url():
    return app.commands.uploads.sas.perform(UploadType.VISIT)


# using a pydantic class for input data validation automatically converts input string json to dates
class VisitAudioUploadRequest(BaseModel):
    file_name: str
    appointment_date: datetime
    patient_name: str
    date_of_birth: datetime


@internal_api_app.post("/visit-audio-file/upload-complete")
def upload_visit_audio_file(request: Request, data: VisitAudioUploadRequest) -> None:
    doctor: Doctor = request.state.doctor

    return app.commands.uploads.upload_visit_audio_file.perform(
        doctor=doctor,
        # TODO(mbianco) model_dump does not retain the types, so we lose type checking on `perform`
        **data.model_dump(),
    )


# for "raw" types like str, we need to explicitly specify the response_model
@internal_api_app.post("/notes/get-signed-url", response_model=str)
def generate_signed_example_note_upload_url():
    return app.commands.uploads.sas.perform(UploadType.EXAMPLE_NOTE)


@internal_api_app.post("/notes/upload-example")
def upload_example_note(request: Request, file_name: str, note_type: str) -> None:
    doctor: Doctor = request.state.doctor

    doctor_note_file = DoctorNoteFile(
        doctor_id=doctor.id, azure_file_name=file_name, note_type=note_type
    ).save()

    app.jobs.extract.queue(doctor_note_file.id)


class NoteTypeCustomizationData(BaseModel, extra="forbid"):
    doctor_note_schema_id: str
    subjective_source: SubjectiveSource
    pronoun_style: PronounStyle
    plan_ordering: PlanOrdering
    flag_new_medications: bool
    require_plan_reason: bool
    ros_distinct_from_subjective: bool
    custom_instructions: str | None


def _customization_data(
    schema_id: TypeIDType, row: NoteTypeCustomization | None
) -> NoteTypeCustomizationData:
    c = row or default_customization()
    return NoteTypeCustomizationData(
        doctor_note_schema_id=str(schema_id),
        subjective_source=c.subjective_source,
        pronoun_style=c.pronoun_style,
        plan_ordering=c.plan_ordering,
        flag_new_medications=c.flag_new_medications,
        require_plan_reason=c.require_plan_reason,
        ros_distinct_from_subjective=c.ros_distinct_from_subjective,
        custom_instructions=c.custom_instructions,
    )


class SettingsData(BaseModel, extra="forbid"):
    doctor_id: str
    notes_schema: list[DoctorNoteSchema]
    biography: str | None
    zoom_connected: bool
    # True until acked when Zoom returned invalid_grant on the doctor's
    # refresh token (uninstall from marketplace.zoom.us, or admin revoke).
    # The frontend uses this to fire a one-time toast; the persistent
    # banner uses zoom_connected alone.
    zoom_revoked: bool
    email: str | None
    onboarding_completed: bool
    note_customizations: list[NoteTypeCustomizationData]
    has_natmed_subscription: bool
    terminology: Terminology


@internal_api_app.get("/settings")
def settings_data(request: Request) -> SettingsData:
    doctor: Doctor = request.state.doctor

    zoom_integration = DoctorVideoIntegration.get(
        doctor_id=doctor.id, provider=VideoProvider.zoom
    )
    zoom_revoked = bool(get_redis().get(zoom_revoked_redis_key(str(doctor.id))))

    notes_schema = cast(
        list[DoctorNoteSchema],
        list(
            DoctorNoteSchema.where(
                (DoctorNoteSchema.doctor_id == doctor.id)
                & cast(Any, DoctorNoteSchema.deleted_at).is_(None)
            ).all()
        ),
    )

    schema_ids = [s.id for s in notes_schema]
    customization_rows = (
        NoteTypeCustomization.where(
            cast(Any, NoteTypeCustomization.doctor_note_schema_id).in_(schema_ids)
        ).all()
        if schema_ids
        else []
    )
    by_schema = {
        r.doctor_note_schema_id: r
        for r in cast(list[NoteTypeCustomization], customization_rows)
    }
    note_customizations = [
        _customization_data(s.id, by_schema.get(s.id)) for s in notes_schema
    ]

    return SettingsData(
        notes_schema=notes_schema,
        doctor_id=str(doctor.id),
        biography=doctor.biography,
        zoom_connected=zoom_integration is not None,
        zoom_revoked=zoom_revoked,
        email=doctor.email,
        onboarding_completed=doctor.onboarding_completed_at is not None,
        note_customizations=note_customizations,
        has_natmed_subscription=doctor.has_natmed_subscription,
        terminology=doctor.terminology,
    )


class UpdateNoteTypeCustomization(BaseModel, extra="forbid"):
    subjective_source: SubjectiveSource = SubjectiveSource.patient_only
    pronoun_style: PronounStyle = PronounStyle.infer_from_transcript
    plan_ordering: PlanOrdering = PlanOrdering.patient_then_doctor
    flag_new_medications: bool = True
    require_plan_reason: bool = True
    ros_distinct_from_subjective: bool = True
    custom_instructions: str | None = Field(default=None, max_length=4000)


@internal_api_app.put(
    "/note-schemas/{schema_id}/customization", name="update_note_customization"
)
def update_note_customization(
    request: Request,
    schema_id: Annotated[TypeIDType, Path()],
    data: UpdateNoteTypeCustomization,
) -> NoteTypeCustomizationData:
    doctor: Doctor = request.state.doctor

    schema = DoctorNoteSchema.get(id=schema_id)
    if schema is None or schema.doctor_id != doctor.id or schema.deleted_at is not None:
        # 404 not 403 — don't leak existence of other doctors' schemas
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    row = NoteTypeCustomization.find_or_initialize_by(doctor_note_schema_id=schema.id)
    row.subjective_source = data.subjective_source
    row.pronoun_style = data.pronoun_style
    row.plan_ordering = data.plan_ordering
    row.flag_new_medications = data.flag_new_medications
    row.require_plan_reason = data.require_plan_reason
    row.ros_distinct_from_subjective = data.ros_distinct_from_subjective
    row.custom_instructions = data.custom_instructions
    row.save()

    return _customization_data(schema.id, row)


class UpdateSettingsData(BaseModel, extra="forbid"):
    biography: str | None
    # optional so existing clients that only send biography keep working
    has_natmed_subscription: bool | None = None
    # optional so clients that only update the biography keep working
    terminology: Terminology | None = None


@internal_api_app.post("/settings")
def update_settings_data(request: Request, data: UpdateSettingsData) -> None:
    doctor: Doctor = request.state.doctor
    doctor.biography = data.biography
    if data.has_natmed_subscription is not None:
        doctor.has_natmed_subscription = data.has_natmed_subscription
    if data.terminology is not None:
        doctor.terminology = data.terminology
    doctor.save()


class ZoomAuthUrlResponse(BaseModel, extra="forbid"):
    url: str


@internal_api_app.get("/zoom-auth-url", name="zoom_auth_url")
def zoom_auth_url(request: Request) -> ZoomAuthUrlResponse:
    doctor: Doctor = request.state.doctor
    base = str(request.base_url).rstrip("/")
    url = f"{base}/external/v1/zoom-oauth-start?state={doctor.clerk_id}"
    return ZoomAuthUrlResponse(url=url)


@internal_api_app.delete(
    "/zoom-connect", name="zoom_disconnect", status_code=status.HTTP_204_NO_CONTENT
)
def zoom_disconnect(request: Request) -> None:
    doctor: Doctor = request.state.doctor
    zoom_service.revoke_token(doctor)
    # An intentional disconnect should not surface as an unexpected revoke
    # toast next time the doctor opens the app.
    get_redis().delete(zoom_revoked_redis_key(str(doctor.id)))


@internal_api_app.post(
    "/zoom-revoked-ack", name="zoom_revoked_ack", status_code=status.HTTP_204_NO_CONTENT
)
def zoom_revoked_ack(request: Request) -> None:
    """Frontend calls this after showing the one-time disconnect toast."""
    doctor: Doctor = request.state.doctor
    get_redis().delete(zoom_revoked_redis_key(str(doctor.id)))


@internal_api_app.post(
    "/onboarding/complete", name="complete_onboarding", status_code=status.HTTP_200_OK
)
def complete_onboarding(request: Request) -> None:
    doctor: Doctor = request.state.doctor
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()


class ZoomMeetingResponse(BaseModel, extra="forbid"):
    id: str
    zoom_meeting_id: str
    topic: str
    start_time: datetime
    duration: int
    join_url: str
    status: ZoomMeetingStatus
    appointment_id: str | None


class ScheduleResponse(BaseModel, extra="forbid"):
    zoom_connected: bool
    meetings: list[ZoomMeetingResponse]


@internal_api_app.get("/schedule", name="schedule_list")
def schedule_list(request: Request) -> ScheduleResponse:
    doctor: Doctor = request.state.doctor

    zoom_integration = get_zoom_integration(doctor)
    if not zoom_integration:
        return ScheduleResponse(zoom_connected=False, meetings=[])

    sync_zoom_meetings.queue(str(doctor.id))

    now = datetime.now(timezone.utc)
    past_cutoff = now - timedelta(days=30)
    future_cutoff = now + timedelta(days=90)

    meetings = (
        ZoomMeeting.where(
            cast(Any, ZoomMeeting.doctor_id) == doctor.id,
            cast(Any, ZoomMeeting.status) != ZoomMeetingStatus.cancelled,
            cast(Any, ZoomMeeting.start_time) >= past_cutoff,
            cast(Any, ZoomMeeting.start_time) <= future_cutoff,
        )
        .order_by(cast(Any, ZoomMeeting.start_time).asc())
        .all()
    )

    return ScheduleResponse(
        zoom_connected=True,
        meetings=[
            ZoomMeetingResponse(
                id=str(m.id),
                zoom_meeting_id=m.zoom_meeting_id,
                topic=m.topic,
                start_time=m.start_time,
                duration=m.duration,
                join_url=m.join_url,
                status=m.status,
                appointment_id=str(m.appointment_id) if m.appointment_id else None,
            )
            for m in cast(list[ZoomMeeting], list(meetings))
        ],
    )


@internal_api_app.delete(
    "/schedule/{zoom_meeting_id}",
    name="cancel_meeting",
    status_code=status.HTTP_204_NO_CONTENT,
)
def cancel_meeting(request: Request, zoom_meeting_id: Annotated[str, Path()]) -> None:
    doctor: Doctor = request.state.doctor

    meeting = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=zoom_meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found"
        )

    zoom_service.delete_meeting(doctor, zoom_meeting_id)

    meeting.status = ZoomMeetingStatus.cancelled
    meeting.save()


class MedicationData(BaseModel, extra="forbid"):
    id: str
    name: str
    # display_name is what a provider should be shown; normalized_name is the
    # lowercased match key and is not meant for display
    display_name: str | None
    normalized_name: str | None
    rxnorm_cui: str | None
    kind: MedicationKind
    status: MedicationStatus
    source: MedicationSource
    dosage: str | None
    confirmed: bool


def _medication_data(row: PatientMedication) -> MedicationData:
    return MedicationData(
        id=str(row.id),
        name=row.name,
        display_name=row.display_name,
        normalized_name=row.normalized_name,
        rxnorm_cui=row.rxnorm_cui,
        kind=row.kind,
        status=row.status,
        source=row.source,
        dosage=row.dosage,
        confirmed=row.confirmed,
    )


def _reference_patient_or_404(doctor: Doctor, patient_id: str) -> Patient:
    if not reference_toolkit_enabled_for(doctor.email):
        # 404 not 403 — don't advertise the gated feature's existence
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    patient = Patient.get(id=patient_id, doctor_id=doctor.id)
    if patient is None:
        # 404 not 403 — don't leak existence of other doctors' patients
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return patient


def _owned_medication_or_404(doctor: Doctor, medication_id: str) -> PatientMedication:
    if not reference_toolkit_enabled_for(doctor.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    row = PatientMedication.get(id=medication_id, doctor_id=doctor.id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return row


@internal_api_app.get("/patients/{patient_id}/medications", name="medication_list")
def medication_list(
    request: Request, patient_id: Annotated[str, Path()]
) -> list[MedicationData]:
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    rows = (
        PatientMedication.where(
            (PatientMedication.doctor_id == doctor.id)
            & (PatientMedication.patient_id == patient.id)
            & cast(Any, PatientMedication.deleted_at).is_(None)
        )
        .order_by(cast(Any, PatientMedication.created_at).desc())
        .all()
    )

    return [_medication_data(r) for r in cast(list[PatientMedication], rows)]


class CreateMedication(BaseModel, extra="forbid"):
    name: str = Field(min_length=1, max_length=300)
    kind: MedicationKind = MedicationKind.unknown
    status: MedicationStatus = MedicationStatus.active
    dosage: str | None = Field(default=None, max_length=300)


@internal_api_app.post("/patients/{patient_id}/medications", name="medication_create")
def medication_create(
    request: Request, patient_id: Annotated[str, Path()], data: CreateMedication
) -> MedicationData:
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    row = PatientMedication(
        doctor_id=doctor.id,
        patient_id=patient.id,
        name=data.name.strip(),
        # manual entry never consults RxNorm, so the provider's own wording is
        # the most authoritative name we have
        display_name=data.name.strip(),
        normalized_name=data.name.strip().lower(),
        kind=data.kind,
        status=data.status,
        source=MedicationSource.manual,
        dosage=data.dosage,
        # manually entered rows are confirmed by definition
        confirmed=True,
        confirmed_at=datetime.now(timezone.utc),
    ).save()

    return _medication_data(row)


class UpdateMedication(BaseModel, extra="forbid"):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    kind: MedicationKind | None = None
    status: MedicationStatus | None = None
    dosage: str | None = Field(default=None, max_length=300)
    confirmed: bool | None = None


@internal_api_app.patch("/medications/{medication_id}", name="medication_update")
def medication_update(
    request: Request,
    medication_id: Annotated[str, Path()],
    data: UpdateMedication,
) -> MedicationData:
    doctor: Doctor = request.state.doctor
    row = _owned_medication_or_404(doctor, medication_id)

    if data.name is not None:
        row.name = data.name.strip()
        row.display_name = data.name.strip()
        row.normalized_name = data.name.strip().lower()
        # name changed by hand; drop the stale RxNorm mapping and its canonical
        row.rxnorm_cui = None
    if data.kind is not None:
        row.kind = data.kind
    if data.status is not None:
        row.status = data.status
    if data.dosage is not None:
        row.dosage = data.dosage
    if data.confirmed is not None and data.confirmed and not row.confirmed:
        row.confirmed = True
        row.confirmed_at = datetime.now(timezone.utc)
    elif data.confirmed is not None and not data.confirmed:
        row.confirmed = False
        row.confirmed_at = None

    row.save()
    return _medication_data(row)


@internal_api_app.delete(
    "/medications/{medication_id}",
    name="medication_delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
def medication_delete(request: Request, medication_id: Annotated[str, Path()]) -> None:
    doctor: Doctor = request.state.doctor
    row = _owned_medication_or_404(doctor, medication_id)
    row.delete()


class ExtractMedications(BaseModel, extra="forbid"):
    appointment_id: str


@internal_api_app.post(
    "/patients/{patient_id}/medications/extract", name="medication_extract"
)
def medication_extract(
    request: Request,
    patient_id: Annotated[str, Path()],
    data: ExtractMedications,
) -> None:
    "re-run extraction on demand for a patient's appointment transcript"
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    transcript = AIVisitTranscript.get(
        appointment_id=data.appointment_id,
        doctor_id=doctor.id,
        patient_id=patient.id,
    )
    if transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    extract_medications_job.queue(transcript_id=transcript.id)


class MedicationResources(BaseModel, extra="forbid"):
    medication: MedicationData
    links: list[ResourceLink]


class PatientResourcePanel(BaseModel, extra="forbid"):
    has_natmed_subscription: bool
    directory: list[ResourceLink]
    medications: list[MedicationResources]


@internal_api_app.get(
    "/patients/{patient_id}/reference-resources", name="patient_reference_resources"
)
def patient_reference_resources(
    request: Request, patient_id: Annotated[str, Path()]
) -> PatientResourcePanel:
    "reference deep links for a patient's confirmed medications, plus the resource directory"
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    confirmed = cast(
        list[PatientMedication],
        list(
            PatientMedication.where(
                (PatientMedication.doctor_id == doctor.id)
                & (PatientMedication.patient_id == patient.id)
                & (PatientMedication.confirmed == True)  # noqa: E712
                & cast(Any, PatientMedication.deleted_at).is_(None)
            )
            .order_by(cast(Any, PatientMedication.created_at).desc())
            .all()
        ),
    )

    medications = [
        MedicationResources(
            medication=_medication_data(row),
            links=resource_links_for(
                row.normalized_name or row.name,
                row.kind,
                has_natmed_subscription=doctor.has_natmed_subscription,
            ),
        )
        for row in confirmed
    ]

    return PatientResourcePanel(
        has_natmed_subscription=doctor.has_natmed_subscription,
        directory=PROVIDER_RESOURCE_DIRECTORY,
        medications=medications,
    )


class DrugReferenceSection(BaseModel, extra="forbid"):
    key: str
    label: str
    text: str
    # True when `text` is a prefix of the real section. Surfaced so a clinician
    # is never shown partial safety text that looks complete.
    truncated: bool = False
    # DailyMed page carrying the full label, for when it is.
    source_url: str | None = None


class MedicationDrugReference(BaseModel, extra="forbid"):
    medication_id: str
    name: str
    status: str  # "ready" | "pending" | "none"
    sections: list[DrugReferenceSection]


class DrugReferencePanel(BaseModel, extra="forbid"):
    source: str
    medications: list[MedicationDrugReference]


@internal_api_app.get(
    "/patients/{patient_id}/medications/drug-reference",
    name="medication_drug_reference",
)
def medication_drug_reference(
    request: Request, patient_id: Annotated[str, Path()]
) -> DrugReferencePanel:
    "cached openFDA label sections for the patient's confirmed drugs (not supplements)"
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    confirmed_drugs = cast(
        list[PatientMedication],
        list(
            PatientMedication.where(
                (PatientMedication.doctor_id == doctor.id)
                & (PatientMedication.patient_id == patient.id)
                & (PatientMedication.confirmed == True)  # noqa: E712
                & (PatientMedication.kind != MedicationKind.supplement)
                & cast(Any, PatientMedication.deleted_at).is_(None)
            )
            .order_by(cast(Any, PatientMedication.created_at).desc())
            .all()
        ),
    )

    entries: list[MedicationDrugReference] = []
    for med in confirmed_drugs:
        key = (med.normalized_name or med.name).strip().lower()
        cached = DrugReference.get(normalized_name=key)

        if cached is None:
            # first time we've seen this drug — fetch in the background
            fetch_drug_reference_job.queue(key)
            status_str = "pending"
            sections = []
        elif cached.found:
            status_str = "ready"
            sections = [DrugReferenceSection(**s) for s in cached.sections]
        else:
            status_str = "none"
            sections = []

        entries.append(
            MedicationDrugReference(
                medication_id=str(med.id),
                name=med.name,
                status=status_str,
                sections=sections,
            )
        )

    return DrugReferencePanel(
        source="openFDA (U.S. FDA, public domain)", medications=entries
    )


class NutrientDepletionCitation(BaseModel, extra="forbid"):
    reference: str
    url: str


class NutrientDepletionEntry(BaseModel, extra="forbid"):
    nutrient: str
    evidence: str
    mechanism: str
    citation: NutrientDepletionCitation


class MedicationDepletions(BaseModel, extra="forbid"):
    medication_id: str
    name: str
    depletions: list[NutrientDepletionEntry]


class NutrientDepletionPanel(BaseModel, extra="forbid"):
    disclaimer: str
    medications: list[MedicationDepletions]


@internal_api_app.get(
    "/patients/{patient_id}/medications/nutrient-depletions",
    name="medication_nutrient_depletions",
)
def medication_nutrient_depletions(
    request: Request, patient_id: Annotated[str, Path()]
) -> NutrientDepletionPanel:
    "curated, citation-backed nutrient depletions for the patient's confirmed drugs"
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    confirmed_drugs = cast(
        list[PatientMedication],
        list(
            PatientMedication.where(
                (PatientMedication.doctor_id == doctor.id)
                & (PatientMedication.patient_id == patient.id)
                & (PatientMedication.confirmed == True)  # noqa: E712
                & (PatientMedication.kind != MedicationKind.supplement)
                & cast(Any, PatientMedication.deleted_at).is_(None)
            )
            .order_by(cast(Any, PatientMedication.created_at).desc())
            .all()
        ),
    )

    medications: list[MedicationDepletions] = []
    for med in confirmed_drugs:
        found = depletions_for(med.normalized_name or med.name)
        if not found:
            continue
        medications.append(
            MedicationDepletions(
                medication_id=str(med.id),
                name=med.name,
                depletions=[
                    NutrientDepletionEntry(
                        nutrient=d.nutrient,
                        evidence=d.evidence,
                        mechanism=d.mechanism,
                        citation=NutrientDepletionCitation(
                            reference=d.citation.reference, url=d.citation.url
                        ),
                    )
                    for d in found
                ],
            )
        )

    return NutrientDepletionPanel(
        disclaimer=DEPLETION_DISCLAIMER, medications=medications
    )


class LabResultData(BaseModel, extra="forbid"):
    id: str
    biomarker: str
    raw_name: str
    value: float
    unit: str | None


class LabPanelData(BaseModel, extra="forbid"):
    id: str
    source: LabPanelSource
    status: LabPanelStatus
    collected_on: str | None
    created_at: datetime | None
    results: list[LabResultData]


def _lab_result_data(row: LabResult) -> LabResultData:
    return LabResultData(
        id=str(row.id),
        biomarker=row.biomarker,
        raw_name=row.raw_name,
        value=row.value,
        unit=row.unit,
    )


def _lab_panel_data(panel: LabPanel, results: list[LabResult]) -> LabPanelData:
    return LabPanelData(
        id=str(panel.id),
        source=panel.source,
        status=panel.status,
        collected_on=panel.collected_on.isoformat() if panel.collected_on else None,
        created_at=panel.created_at,
        results=[_lab_result_data(r) for r in results],
    )


@internal_api_app.get("/patients/{patient_id}/lab-panels", name="lab_panel_list")
def lab_panel_list(
    request: Request, patient_id: Annotated[str, Path()]
) -> list[LabPanelData]:
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    panels = cast(
        list[LabPanel],
        list(
            LabPanel.where(
                (LabPanel.doctor_id == doctor.id)
                & (LabPanel.patient_id == patient.id)
                & cast(Any, LabPanel.deleted_at).is_(None)
            )
            .order_by(cast(Any, LabPanel.created_at).desc())
            .all()
        ),
    )

    out: list[LabPanelData] = []
    for panel in panels:
        results = cast(
            list[LabResult],
            list(LabResult.where(LabResult.lab_panel_id == panel.id).all()),
        )
        out.append(_lab_panel_data(panel, results))
    return out


class ManualLabResult(BaseModel, extra="forbid"):
    name: str = Field(min_length=1, max_length=200)
    value: float
    unit: str | None = Field(default=None, max_length=40)


class CreateLabPanel(BaseModel, extra="forbid"):
    collected_on: str | None = None
    results: list[ManualLabResult] = Field(min_length=1)


@internal_api_app.post("/patients/{patient_id}/lab-panels", name="lab_panel_create")
def lab_panel_create(
    request: Request, patient_id: Annotated[str, Path()], data: CreateLabPanel
) -> LabPanelData:
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    collected = None
    if data.collected_on:
        try:
            collected = datetime.fromisoformat(data.collected_on).date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="collected_on must be an ISO date",
            )

    panel = LabPanel(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=LabPanelSource.manual,
        status=LabPanelStatus.completed,
        collected_on=collected,
    ).save()

    results: list[LabResult] = []
    for item in data.results:
        key = normalize_biomarker(item.name)
        results.append(
            LabResult(
                lab_panel_id=panel.id,
                doctor_id=doctor.id,
                patient_id=patient.id,
                biomarker=key or item.name.strip().lower(),
                raw_name=item.name.strip(),
                value=item.value,
                unit=item.unit or (default_unit(key) if key else None),
            ).save()
        )

    return _lab_panel_data(panel, results)


@internal_api_app.post("/lab-panels/get-signed-url", response_model=str)
def generate_signed_lab_upload_url():
    return app.commands.uploads.sas.perform(UploadType.LAB)


class LabUploadComplete(BaseModel, extra="forbid"):
    file_name: str
    collected_on: str | None = None


@internal_api_app.post(
    "/patients/{patient_id}/lab-panels/upload-complete",
    name="lab_panel_upload_complete",
)
def lab_panel_upload_complete(
    request: Request, patient_id: Annotated[str, Path()], data: LabUploadComplete
) -> LabPanelData:
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    collected = None
    if data.collected_on:
        try:
            collected = datetime.fromisoformat(data.collected_on).date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="collected_on must be an ISO date",
            )

    panel = LabPanel(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=LabPanelSource.upload,
        status=LabPanelStatus.pending,
        azure_file_name=data.file_name,
        collected_on=collected,
    ).save()

    extract_labs_job.queue(panel.id)

    return _lab_panel_data(panel, [])


@internal_api_app.delete(
    "/lab-panels/{lab_panel_id}",
    name="lab_panel_delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
def lab_panel_delete(request: Request, lab_panel_id: Annotated[str, Path()]) -> None:
    doctor: Doctor = request.state.doctor
    if not reference_toolkit_enabled_for(doctor.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    panel = LabPanel.get(id=lab_panel_id, doctor_id=doctor.id)
    if panel is None or panel.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # soft delete (the mixin's soft_delete() is an unimplemented stub): set the
    # tombstone and save. Leaves child LabResult rows intact; the list query
    # filters on deleted_at.
    panel.deleted_at = datetime.now(timezone.utc)
    panel.save()


def _effective_range(
    doctor_id, biomarker: str
) -> tuple[lab_ranges_service.RangeSpec | None, LabRangeSet | None]:
    "provider override if present, else platform default"
    override = LabRangeSet.get(doctor_id=doctor_id, biomarker=biomarker)
    if override is not None:
        spec = lab_ranges_service.RangeSpec(
            unit=override.unit or (default_unit(biomarker) or ""),
            standard_low=override.standard_low,
            standard_high=override.standard_high,
            optimal_low=override.optimal_low,
            optimal_high=override.optimal_high,
        )
        return spec, override
    return lab_ranges_service.default_range(biomarker), None


class LabEvaluationEntry(BaseModel, extra="forbid"):
    biomarker: str
    display_name: str
    value: float
    unit: str | None
    collected_on: str | None
    classification: str
    standard_low: float | None
    standard_high: float | None
    optimal_low: float | None
    optimal_high: float | None
    source_url: str | None


class LabEvaluationPanel(BaseModel, extra="forbid"):
    entries: list[LabEvaluationEntry]


@internal_api_app.get("/patients/{patient_id}/lab-evaluations", name="lab_evaluations")
def lab_evaluations(
    request: Request, patient_id: Annotated[str, Path()]
) -> LabEvaluationPanel:
    "latest value per biomarker, classified against the effective dual range"
    doctor: Doctor = request.state.doctor
    patient = _reference_patient_or_404(doctor, patient_id)

    # most recent result per biomarker: walk panels newest-first (by specimen
    # date when known, else by row creation), keep the first seen per marker
    panels = cast(
        list[LabPanel],
        list(
            LabPanel.where(
                (LabPanel.doctor_id == doctor.id)
                & (LabPanel.patient_id == patient.id)
                & cast(Any, LabPanel.deleted_at).is_(None)
            )
            .order_by(
                cast(Any, LabPanel.collected_on).desc().nullslast(),
                cast(Any, LabPanel.created_at).desc(),
            )
            .all()
        ),
    )

    seen: set[str] = set()
    entries: list[LabEvaluationEntry] = []
    for panel in panels:
        results = cast(
            list[LabResult],
            list(LabResult.where(LabResult.lab_panel_id == panel.id).all()),
        )
        for result in results:
            if result.biomarker in seen:
                continue
            seen.add(result.biomarker)

            spec, override = _effective_range(doctor.id, result.biomarker)
            classification = lab_ranges_service.classify(result.value, spec)

            entries.append(
                LabEvaluationEntry(
                    biomarker=result.biomarker,
                    display_name=biomarker_display_name(result.biomarker),
                    value=result.value,
                    unit=result.unit,
                    collected_on=(
                        panel.collected_on.isoformat() if panel.collected_on else None
                    ),
                    classification=classification,
                    standard_low=spec.standard_low if spec else None,
                    standard_high=spec.standard_high if spec else None,
                    optimal_low=spec.optimal_low if spec else None,
                    optimal_high=spec.optimal_high if spec else None,
                    source_url=override.source_url if override else None,
                )
            )

    return LabEvaluationPanel(entries=entries)


class LabRangeData(BaseModel, extra="forbid"):
    biomarker: str
    display_name: str
    unit: str | None
    standard_low: float | None
    standard_high: float | None
    optimal_low: float | None
    optimal_high: float | None
    source_url: str | None
    is_override: bool


def _lab_range_data(biomarker: str, doctor_id) -> LabRangeData:
    spec, override = _effective_range(doctor_id, biomarker)
    return LabRangeData(
        biomarker=biomarker,
        display_name=biomarker_display_name(biomarker),
        unit=(override.unit if override else None) or default_unit(biomarker),
        standard_low=spec.standard_low if spec else None,
        standard_high=spec.standard_high if spec else None,
        optimal_low=spec.optimal_low if spec else None,
        optimal_high=spec.optimal_high if spec else None,
        source_url=override.source_url if override else None,
        is_override=override is not None,
    )


@internal_api_app.get("/lab-ranges", name="lab_range_list")
def lab_range_list(request: Request) -> list[LabRangeData]:
    "effective ranges (defaults merged with this doctor's overrides) for all biomarkers"
    doctor: Doctor = request.state.doctor
    if not reference_toolkit_enabled_for(doctor.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    return [_lab_range_data(key, doctor.id) for key in BIOMARKERS_BY_KEY]


class UpdateLabRange(BaseModel, extra="forbid"):
    unit: str | None = Field(default=None, max_length=40)
    standard_low: float | None = None
    standard_high: float | None = None
    optimal_low: float | None = None
    optimal_high: float | None = None
    source_url: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _bounds_must_be_coherent(self):
        """Reject range sets that would silently mislabel every result.

        classify() treats a value as out of range when it is below `low` OR
        above `high`. If a provider transposes two numbers so that low > high,
        both tests pass for every value and *every* lab that provider views is
        flagged abnormal — with no error anywhere to explain it.
        """

        for label, low, high in (
            ("standard", self.standard_low, self.standard_high),
            ("optimal", self.optimal_low, self.optimal_high),
        ):
            if low is not None and high is not None and low > high:
                raise ValueError(
                    f"{label}_low must be less than or equal to {label}_high"
                )

        for name, value in (
            ("standard_low", self.standard_low),
            ("standard_high", self.standard_high),
            ("optimal_low", self.optimal_low),
            ("optimal_high", self.optimal_high),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")

        return self


@internal_api_app.put("/lab-ranges/{biomarker}", name="lab_range_upsert")
def lab_range_upsert(
    request: Request, biomarker: Annotated[str, Path()], data: UpdateLabRange
) -> LabRangeData:
    doctor: Doctor = request.state.doctor
    if not reference_toolkit_enabled_for(doctor.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if biomarker not in BIOMARKERS_BY_KEY:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    row = LabRangeSet.find_or_initialize_by(doctor_id=doctor.id, biomarker=biomarker)
    row.unit = data.unit
    row.standard_low = data.standard_low
    row.standard_high = data.standard_high
    row.optimal_low = data.optimal_low
    row.optimal_high = data.optimal_high
    row.source_url = data.source_url
    row.save()

    return _lab_range_data(biomarker, doctor.id)


@internal_api_app.delete(
    "/lab-ranges/{biomarker}",
    name="lab_range_reset",
    status_code=status.HTTP_204_NO_CONTENT,
)
def lab_range_reset(request: Request, biomarker: Annotated[str, Path()]) -> None:
    "remove the provider override, reverting to the platform default"
    doctor: Doctor = request.state.doctor
    if not reference_toolkit_enabled_for(doctor.email):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    row = LabRangeSet.get(doctor_id=doctor.id, biomarker=biomarker)
    if row is not None:
        row.delete()
