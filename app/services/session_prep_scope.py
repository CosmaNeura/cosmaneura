"""Fail-closed scope resolution for the synthetic Session Prep demonstration."""

from __future__ import annotations

from typing import Any, cast

from typeid import TypeID
from typeid.errors import TypeIDException

from app.configuration.session_prep import (
    SESSION_PREP_SYNTHETIC_SETTINGS,
    SessionPrepSyntheticSettings,
)
from app.environments import is_staging
from app.services.staging_synthetic_appointment import (
    StagingSyntheticAppointmentConflict,
    validate_exact_synthetic_fixture,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.synthetic_preview_appointment_fixture import (
    SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    SyntheticPreviewAppointmentFixture,
)
from sqlmodel import Session, select


def synthetic_session_prep_context_ref(
    *,
    clerk_subject: str,
    record_ref: str,
    session: Session | None = None,
    settings: SessionPrepSyntheticSettings | None = None,
) -> str | None:
    """Return the configured ref only for the exact intact synthetic fixture.

    The returned value is routing context, not authority. Callers must still
    enforce the provider binding and capability at their own boundary. Every
    mismatch is an ordinary ineligible result so an unrelated record never
    learns why the synthetic preview scope was withheld.
    """

    active_settings = settings or SESSION_PREP_SYNTHETIC_SETTINGS
    if not active_settings.is_complete() or not is_staging():
        return None
    if (
        not isinstance(clerk_subject, str)
        or not clerk_subject
        or not isinstance(record_ref, str)
        or not record_ref
        or clerk_subject != active_settings.clerk_subject
        or record_ref != active_settings.appointment_id
    ):
        return None

    try:
        appointment_id = TypeID.from_string(record_ref)
        fixture_id = TypeID.from_string(active_settings.fixture_id)
    except TypeIDException:
        return None
    if (
        appointment_id.prefix != "appointment"
        or fixture_id.prefix != "synthetic_preview_fixture"
    ):
        return None

    if session is None:
        with get_session() as managed_session:
            return synthetic_session_prep_context_ref(
                clerk_subject=clerk_subject,
                record_ref=record_ref,
                session=cast(Session, managed_session),
                settings=active_settings,
            )

    doctor = session.exec(
        select(Doctor).where(Doctor.clerk_id == clerk_subject)
    ).one_or_none()
    if (
        doctor is None
        or doctor.deleted_at is not None
        or doctor.last_active_at is None
        or doctor.clerk_id != clerk_subject
    ):
        return None

    fixture = session.exec(
        select(SyntheticPreviewAppointmentFixture).where(
            SyntheticPreviewAppointmentFixture.id == cast(TypeIDType, fixture_id),
            SyntheticPreviewAppointmentFixture.doctor_id == doctor.id,
            SyntheticPreviewAppointmentFixture.appointment_id
            == cast(TypeIDType, appointment_id),
            cast(Any, SyntheticPreviewAppointmentFixture.synthetic_only).is_(True),
            SyntheticPreviewAppointmentFixture.fixture_version
            == SYNTHETIC_PREVIEW_FIXTURE_VERSION,
        )
    ).one_or_none()
    if fixture is None:
        return None

    appointment = session.get(Appointment, fixture.appointment_id)
    patient = session.get(Patient, fixture.patient_id)
    # Appointment and Patient do not currently implement soft deletion. Keep
    # this check fail-closed if either model gains that lifecycle later.
    if (
        getattr(appointment, "deleted_at", None) is not None
        or getattr(patient, "deleted_at", None) is not None
    ):
        return None
    try:
        validate_exact_synthetic_fixture(
            doctor=doctor,
            fixture=fixture,
            patient=patient,
            appointment=appointment,
        )
    except StagingSyntheticAppointmentConflict:
        return None

    return active_settings.context_snapshot_ref
