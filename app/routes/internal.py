from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, Field, computed_field, model_validator

import app.commands.scribe_transcript
import app.commands.uploads.sas
import app.commands.uploads.upload_visit_audio_file
import app.jobs.extract
import app.jobs.sync_zoom_meetings as sync_zoom_meetings
import app.services.zoom as zoom_service
from app.commands.uploads.sas import UploadType
from app.configuration.redis import get_redis
from app.services.zoom import get_zoom_integration, zoom_revoked_redis_key

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor, Terminology
from app.models.doctor_note_file import DoctorNoteFile
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.engagement_role import EngagementRole
from app.models.intake.intake import Intake
from app.models.note_type_customization import (
    NoteTypeCustomization,
    PlanOrdering,
    PronounStyle,
    SubjectiveSource,
    default_customization,
)
from app.models.patient import Patient
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


class ScribeTranscriptEntry(BaseModel, extra="forbid"):
    speaker: Literal["Doctor", "Patient", "Other", "Unknown"]
    content: str = Field(min_length=1)
    startTime: int = Field(ge=0)
    endTime: int = Field(gt=0)

    @model_validator(mode="after")
    def timestamps_are_ordered(self):
        if self.endTime <= self.startTime:
            raise ValueError("endTime must be greater than startTime")
        return self


class ScribeTranscriptImport(BaseModel, extra="forbid"):
    doctor_id: TypeIDType
    appointment_id: TypeIDType
    patient_id: TypeIDType
    duration: int = Field(ge=0)
    transcript_data: list[ScribeTranscriptEntry] = Field(min_length=1)


@internal_api_app.post("/scribe/transcripts", response_model=AIVisitTranscript)
def import_scribe_transcript(
    request: Request,
    data: ScribeTranscriptImport,
    _idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
) -> AIVisitTranscript:
    doctor: Doctor = request.state.doctor
    appointment = Appointment.get(id=data.appointment_id)
    if (
        appointment is None
        or appointment.doctor_id != doctor.id
        or data.doctor_id != appointment.doctor_id
        or data.patient_id != appointment.patient_id
    ):
        raise HTTPException(status_code=404, detail="Appointment not found.")

    try:
        return app.commands.scribe_transcript.perform(
            appointment=appointment,
            duration=data.duration,
            transcript_data=[
                TranscriptEntry(**entry.model_dump()) for entry in data.transcript_data
            ],
        )
    except app.commands.scribe_transcript.TranscriptConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    # optional so clients that only update the biography keep working
    terminology: Terminology | None = None


@internal_api_app.post("/settings")
def update_settings_data(request: Request, data: UpdateSettingsData) -> None:
    doctor: Doctor = request.state.doctor
    doctor.biography = data.biography
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
