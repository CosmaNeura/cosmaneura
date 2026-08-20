import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field, computed_field

import app.commands.uploads.sas
import app.commands.uploads.upload_visit_audio_file
import app.jobs.extract
import app.jobs.sync_zoom_meetings as sync_zoom_meetings
import app.services.ehr_sandbox_record_mapping as ehr_sandbox_record_mapping
import app.services.zoom as zoom_service
from app.commands.uploads.sas import UploadType
from app.configuration.redis import get_redis
from app.services.sovereign_record_grant import (
    RECORD_GRANT_PATTERN,
    RecordGrantConfigurationError,
    configured_record_grant_key,
)
from app.services.zoom import get_zoom_integration, zoom_revoked_redis_key
from app.utils.email_domain import (
    MCD_EMAIL_DOMAIN,
    email_matches_domain,
    extract_email_domain,
    is_mcd_email,
)

from activemodel.session_manager import aglobal_session
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
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
from app.models.practice import OriginChannel, Practice, PracticeMember, PracticeRole
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


BAA_VERSION = "2026-08-19-ux-1"
_JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _make_join_code() -> str:
    return "".join(secrets.choice(_JOIN_ALPHABET) for _ in range(8))


def _normalize_join_code(value: str) -> str:
    return value.strip().upper().replace(" ", "").replace("-", "")


def _membership_for(doctor: Doctor) -> PracticeMember | None:
    return PracticeMember.get(doctor_id=doctor.id)


def _practice_payload(
    practice: Practice | None, member: PracticeMember | None
) -> "PracticeStatus | None":
    if not practice or not member:
        return None
    return PracticeStatus(
        legal_name=practice.legal_name,
        join_code=practice.join_code,
        email_domain=practice.email_domain,
        origin_channel=practice.origin_channel,
        pastoral_pack=practice.pastoral_pack,
        baa_accepted=practice.baa_accepted_at is not None,
        role=member.role,
        attested=member.attested_at is not None,
    )


class PracticeStatus(BaseModel, extra="forbid"):
    legal_name: str
    join_code: str
    email_domain: str
    origin_channel: str
    pastoral_pack: bool
    baa_accepted: bool
    role: str
    attested: bool


class JoinMismatch(BaseModel, extra="forbid"):
    practice_name: str
    required_domain: str
    signed_in_domain: str | None


class PracticeLookupResult(BaseModel, extra="forbid"):
    practice: PracticeStatus | None = None
    mismatch: JoinMismatch | None = None


@internal_api_app.get("/practice", name="practice_status")
def practice_status(request: Request) -> PracticeStatus | None:
    doctor: Doctor = request.state.doctor
    member = _membership_for(doctor)
    if not member:
        return None
    practice = Practice.get(id=member.practice_id)
    return _practice_payload(practice, member)


class CreatePracticeBody(BaseModel, extra="forbid"):
    legal_name: str = Field(min_length=3, max_length=160)
    npi: str | None = Field(default=None, max_length=10)
    city: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=40)
    is_solo: bool = True
    channel: str = "direct"
    pastoral_pack: bool = False


@internal_api_app.post("/practice", name="create_practice")
def create_practice(request: Request, data: CreatePracticeBody) -> PracticeStatus:
    doctor: Doctor = request.state.doctor
    existing = _membership_for(doctor)
    if existing:
        practice = Practice.get(id=existing.practice_id)
        payload = _practice_payload(practice, existing)
        assert payload
        return payload

    domain = extract_email_domain(doctor.email)
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A work email on this account is required to found a practice.",
        )

    channel = (
        data.channel
        if data.channel in {item.value for item in OriginChannel}
        else OriginChannel.direct.value
    )
    email_domain = domain
    if channel == "mcd":
        if not is_mcd_email(doctor.email):
            channel = "catholic"
        else:
            email_domain = MCD_EMAIL_DOMAIN

    join_code = _make_join_code()
    for _ in range(5):
        if Practice.get(join_code=join_code) is None:
            break
        join_code = _make_join_code()

    npi = "".join(ch for ch in (data.npi or "") if ch.isdigit())[:10] or None
    practice = Practice(
        legal_name=data.legal_name.strip(),
        npi=npi,
        city=(data.city or "").strip() or None,
        region=(data.region or "").strip() or None,
        is_solo=data.is_solo,
        join_code=join_code,
        email_domain=email_domain,
        origin_channel=channel,
        pastoral_pack=data.pastoral_pack,
    ).save()
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role=PracticeRole.owner.value,
    ).save()
    payload = _practice_payload(practice, member)
    assert payload
    return payload


class AcceptBaaBody(BaseModel, extra="forbid"):
    signature_name: str = Field(min_length=2, max_length=120)
    signature_title: str = Field(min_length=2, max_length=120)
    checks: list[str]


@internal_api_app.post("/practice/baa", name="accept_practice_baa")
def accept_practice_baa(request: Request, data: AcceptBaaBody) -> PracticeStatus:
    required = {"authority", "read", "accept", "phi"}
    if not required.issubset(set(data.checks)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All BAA acknowledgements are required.",
        )
    doctor: Doctor = request.state.doctor
    member = _membership_for(doctor)
    if not member or member.role != PracticeRole.owner.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the practice owner can accept the BAA.",
        )
    practice = Practice.get(id=member.practice_id)
    assert practice
    if practice.baa_accepted_at is None:
        practice.baa_version = BAA_VERSION
        practice.baa_accepted_at = datetime.now(timezone.utc)
        practice.baa_accepted_by_doctor_id = doctor.id
        practice.baa_signature_name = data.signature_name.strip()
        practice.baa_signature_title = data.signature_title.strip()
        practice.save()
    member.attested_at = datetime.now(timezone.utc)
    member.attestation_name = data.signature_name.strip()
    member.save()
    payload = _practice_payload(practice, member)
    assert payload
    return payload


class JoinCodeBody(BaseModel, extra="forbid"):
    join_code: str = Field(min_length=4, max_length=16)


@internal_api_app.post("/practice/lookup", name="lookup_practice")
def lookup_practice(request: Request, data: JoinCodeBody) -> PracticeLookupResult:
    doctor: Doctor = request.state.doctor
    code = _normalize_join_code(data.join_code)
    practice = Practice.get(join_code=code)
    if not practice:
        return PracticeLookupResult()
    if not email_matches_domain(doctor.email, practice.email_domain):
        return PracticeLookupResult(
            mismatch=JoinMismatch(
                practice_name=practice.legal_name,
                required_domain=practice.email_domain,
                signed_in_domain=extract_email_domain(doctor.email),
            )
        )
    return PracticeLookupResult(
        practice=PracticeStatus(
            legal_name=practice.legal_name,
            join_code=practice.join_code,
            email_domain=practice.email_domain,
            origin_channel=practice.origin_channel,
            pastoral_pack=practice.pastoral_pack,
            baa_accepted=practice.baa_accepted_at is not None,
            role=PracticeRole.workforce.value,
            attested=False,
        )
    )


class JoinPracticeBody(BaseModel, extra="forbid"):
    join_code: str = Field(min_length=4, max_length=16)
    attestation_name: str = Field(min_length=2, max_length=120)
    checks: list[str]


@internal_api_app.post("/practice/join", name="join_practice")
def join_practice(request: Request, data: JoinPracticeBody) -> PracticeStatus:
    required = {"workforce", "not-party", "direction", "phi"}
    if not required.issubset(set(data.checks)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All workforce acknowledgements are required.",
        )
    doctor: Doctor = request.state.doctor
    existing = _membership_for(doctor)
    if existing:
        practice = Practice.get(id=existing.practice_id)
        payload = _practice_payload(practice, existing)
        assert payload
        return payload
    code = _normalize_join_code(data.join_code)
    practice = Practice.get(join_code=code)
    if not practice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No practice matches that join code.",
        )
    if not email_matches_domain(doctor.email, practice.email_domain):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{practice.legal_name} is for @{practice.email_domain} inboxes. "
                "Start your own Cosmas practice instead."
            ),
        )
    if practice.baa_accepted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This practice has not accepted the Cosmaneura Inc BAA yet.",
        )
    member = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role=PracticeRole.workforce.value,
        attested_at=datetime.now(timezone.utc),
        attestation_name=data.attestation_name.strip(),
    ).save()
    payload = _practice_payload(practice, member)
    assert payload
    return payload


@internal_api_app.post(
    "/onboarding/complete", name="complete_onboarding", status_code=status.HTTP_200_OK
)
def complete_onboarding(request: Request) -> None:
    doctor: Doctor = request.state.doctor
    member = _membership_for(doctor)
    if member:
        practice = Practice.get(id=member.practice_id)
        if not practice or not practice.baa_accepted_at or not member.attested_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The Cosmaneura Inc BAA must be in force before Cosmas will process PHI.",
            )
    elif doctor.onboarding_completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name or join a practice and accept the BAA before using Cosmas.",
        )
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
