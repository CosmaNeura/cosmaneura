"""Add only synthetic input fixtures, never identity, consent, or billing state."""

from datetime import datetime, timedelta

from app.configuration.clerk import manual_qa_identity_owned
from app.environments import is_staging
from app.services.clerk_identity import verified_primary_email
from app.services.doctor_access import resolve_doctor_access

from activemodel.session_manager import get_session
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from sqlmodel import select

QA_PRACTICE_NAME = "Cosmaneura Manual QA - Synthetic Practice"
QA_ROLES = {"biller@cosmaneura.com": "owner", "doctor@cosmaneura.com": "workforce"}
QA_PATIENT_NAME = "SYNTHETIC QA - Alex Example"
QA_VISIT_KEY = "cosmaneura-manual-qa-v1"


def seed_manual_qa_fixtures(practice_id: str) -> None:
    if not is_staging():
        raise ValueError("Manual QA fixtures require staging")
    with get_session() as session:
        # One lock serializes reruns for this practice. No resets or deletes.
        practice = session.exec(
            select(Practice).where(Practice.id == practice_id).with_for_update()
        ).one()
        if (
            practice.legal_name != QA_PRACTICE_NAME
            or practice.billing_exempt
            or not practice.stripe_subscription_id
            or practice.subscription_status not in {"active", "trialing"}
        ):
            raise ValueError("Exact QA practice and real test subscription required")
        doctors = []
        for email, role in QA_ROLES.items():
            doctor = session.exec(select(Doctor).where(Doctor.email == email)).one()
            member = session.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
            ).one()
            decision = resolve_doctor_access(doctor)
            if (
                member.practice_id != practice.id
                or member.role != role
                or not manual_qa_identity_owned(doctor.clerk_id)
                or verified_primary_email(doctor.clerk_id) != email
                or doctor.onboarding_completed_at is None
                or not decision.allowed
                or not decision.baa_in_force
                or not decision.entitlement_in_force
            ):
                raise ValueError(
                    "Both seed-owned accounts must finish normal onboarding"
                )
            doctors.append(doctor)
        # Preflight every actor before inserting anything; commit both together.
        for doctor in doctors:
            patient = session.exec(
                select(Patient).where(
                    Patient.doctor_id == doctor.id, Patient.name == QA_PATIENT_NAME
                )
            ).one_or_none()
            if patient is None:
                patient = Patient(doctor_id=doctor.id, name=QA_PATIENT_NAME)
                session.add(patient)
                session.flush()
            for offset, label in ((-7, "Previous visit"), (1, "Upcoming visit")):
                key = f"{QA_VISIT_KEY}:{offset}"
                existing = session.exec(
                    select(Appointment).where(
                        Appointment.doctor_id == doctor.id,
                        Appointment.external_id == key,
                    )
                ).one_or_none()
                if existing is not None:
                    if (
                        existing.patient_id != patient.id
                        or existing.created_by_system != "manual_qa_seed"
                    ):
                        raise ValueError(
                            "Existing QA visit has a different patient scope"
                        )
                    continue
                session.add(
                    Appointment(
                        doctor_id=doctor.id,
                        patient_id=patient.id,
                        external_id=key,
                        created_by_system="manual_qa_seed",
                        appointment_date=datetime.now().replace(
                            hour=14, minute=0, second=0, microsecond=0
                        )
                        + timedelta(days=offset),
                        appointment_name=f"SYNTHETIC QA - {label}",
                        appointment_notes_preview="Fictional test visit. No real patient information.",
                    )
                )
        session.commit()
