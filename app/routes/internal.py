from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, EmailStr, Field, computed_field, field_validator

import app.commands.intake.manual_create
import app.commands.intake.send_link
import app.commands.uploads.sas
import app.commands.uploads.upload_visit_audio_file
import app.jobs.extract
import app.jobs.sync_zoom_meetings as sync_zoom_meetings
import app.services.zoom as zoom_service
from app.commands.uploads.sas import UploadType
from app.configuration.redis import get_redis
from app.constants import MAX_INTAKE_QUESTIONS
from app.services.zoom import get_zoom_integration, zoom_revoked_redis_key

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
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


class CreateIntakeRequest(BaseModel, extra="forbid"):
    patient_name: str = Field(min_length=1, max_length=200)
    date_of_birth: date
    email: EmailStr
    appointment_date: datetime
    send_email: bool = True
    "queue the intake link email immediately after creating the intake"

    @field_validator("appointment_date")
    @classmethod
    def appointment_in_future(cls, value: datetime) -> datetime:
        if value.astimezone(timezone.utc) < datetime.now(timezone.utc):
            raise ValueError("Appointment date must be in the future")

        return value


class CreateIntakeResponse(BaseModel, extra="forbid"):
    appointment_id: TypeIDType
    intake_id: TypeIDType
    intake_url: str
    email_sent_to: str | None


@internal_api_app.post("/intake", name="create_manual_intake")
def create_manual_intake(
    request: Request, data: CreateIntakeRequest
) -> CreateIntakeResponse:
    """
    Create an intake directly from the UI — independent of the external
    scheduling sync — and (by default) email the chat link to the patient.
    """
    doctor: Doctor = request.state.doctor

    intake = app.commands.intake.manual_create.perform(
        doctor=doctor,
        patient_name=data.patient_name,
        date_of_birth=data.date_of_birth,
        patient_email=data.email,
        appointment_date=data.appointment_date,
    )

    email_sent_to = None
    if data.send_email:
        # the email was just stored on the patient by manual_create
        email_sent_to = app.commands.intake.send_link.perform(intake)

    return CreateIntakeResponse(
        appointment_id=intake.appointment_id,
        intake_id=intake.id,
        intake_url=str(intake.public_url),
        email_sent_to=email_sent_to,
    )


class SendIntakeLinkRequest(BaseModel, extra="forbid"):
    email: EmailStr | None = None
    "when provided, saved to the patient record and used; otherwise the stored patient email is used"


class SendIntakeLinkResponse(BaseModel, extra="forbid"):
    email_sent_to: str


@internal_api_app.post("/intake/{appointment_id}/send", name="send_intake_link")
def send_intake_link(
    request: Request,
    appointment_id: Annotated[TypeIDType, Path()],
    data: SendIntakeLinkRequest,
) -> SendIntakeLinkResponse:
    "email (or re-email) the intake chat link to the patient for an existing appointment"
    doctor: Doctor = request.state.doctor

    appointment = Appointment.get(id=appointment_id)
    # 404 not 403 — don't leak existence of other doctors' appointments
    if appointment is None or appointment.doctor_id != doctor.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    intake = cast(Intake | None, appointment.intake)
    if intake is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if appointment.is_expired():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The appointment is in the past, so its intake link has expired",
        )

    try:
        email_sent_to = app.commands.intake.send_link.perform(intake, data.email)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )

    return SendIntakeLinkResponse(email_sent_to=email_sent_to)


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
    custom_intake_questions: list[str]


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
        custom_intake_questions=doctor.custom_intake_questions or [],
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


@internal_api_app.post("/settings")
def update_settings_data(request: Request, data: UpdateSettingsData) -> None:
    doctor: Doctor = request.state.doctor
    doctor.biography = data.biography
    doctor.save()


class UpdateIntakeQuestions(BaseModel, extra="forbid"):
    questions: list[str] = Field(max_length=MAX_INTAKE_QUESTIONS)

    @field_validator("questions")
    @classmethod
    def clean_questions(cls, questions: list[str]) -> list[str]:
        cleaned = [question.strip() for question in questions]

        if any(not question for question in cleaned):
            raise ValueError("Questions cannot be empty")

        if any(len(question) > 500 for question in cleaned):
            raise ValueError("Questions must be 500 characters or fewer")

        if len({question.casefold() for question in cleaned}) != len(cleaned):
            raise ValueError("Questions must be unique")

        return cleaned


@internal_api_app.put("/settings/intake-questions", name="update_intake_questions")
def update_intake_questions(request: Request, data: UpdateIntakeQuestions) -> list[str]:
    """
    Set the exact questions the intake chat asks this doctor's patients
    (max MAX_INTAKE_QUESTIONS). An empty list reverts to AI-generated questions.
    Only affects intakes created after the change.
    """
    doctor: Doctor = request.state.doctor
    doctor.custom_intake_questions = data.questions or None
    doctor.save()

    return doctor.custom_intake_questions or []


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
