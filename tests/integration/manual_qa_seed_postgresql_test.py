"""Real schema, authorization and locking; Clerk responses remain synthetic."""

import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest

from app.configuration import clerk as clerk_configuration
from app.services import manual_qa_seed

from activemodel.session_manager import get_session
from app.models.appointment import Appointment
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from sqlmodel import select

from tests.factories import save_practice_member


@pytest.fixture
def qa_practice(monkeypatch: pytest.MonkeyPatch):
    from app import environments

    monkeypatch.setattr(environments, "is_staging", lambda: True)
    monkeypatch.setattr(manual_qa_seed, "is_staging", lambda: True)
    monkeypatch.setattr(clerk_configuration, "CLERK_PRIVATE_KEY", "sk_test_synthetic")
    now = datetime.now(timezone.utc)
    practice = Practice(
        legal_name=manual_qa_seed.QA_PRACTICE_NAME,
        join_code=f"Q{secrets.token_hex(4).upper()[:7]}",
        email_domain="cosmaneura.com",
        is_solo=False,
        baa_accepted_at=now,
        baa_version=BAA_CURRENT_REFERENCE,
        stripe_subscription_id=f"sub_synthetic_{secrets.token_hex(8)}",
        subscription_status="active",
    ).save()
    doctors, identities = [], {}
    for email, role in manual_qa_seed.QA_ROLES.items():
        doctor = Doctor(
            clerk_id=f"user_manual_qa_{secrets.token_hex(8)}",
            email=email,
            biography="",
            onboarding_completed_at=now,
        ).save()
        save_practice_member(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=doctor.id,
                role=role,
                attested_at=now,
                attestation_name="Fictional QA Clinician",
            )
        )
        doctors.append(doctor)
        identities[doctor.clerk_id] = SimpleNamespace(
            id=doctor.clerk_id,
            banned=False,
            locked=False,
            private_metadata={"manual_qa_seed": clerk_configuration.MANUAL_QA_SEED},
            primary_email_address_id="primary",
            email_addresses=[
                SimpleNamespace(
                    id="primary",
                    email_address=email,
                    verification=SimpleNamespace(status="verified"),
                )
            ],
        )
    monkeypatch.setattr(
        clerk_configuration.clerk,
        "users",
        SimpleNamespace(get=lambda *, user_id: identities[user_id]),
    )
    return practice, doctors, identities


def _fixtures():
    with get_session() as session:
        patients = session.exec(select(Patient)).all()
        visits = session.exec(select(Appointment)).all()
        return (
            {str(p.id): (str(p.doctor_id), p.name) for p in patients},
            {
                str(v.id): (
                    str(v.doctor_id),
                    str(v.patient_id),
                    v.external_id,
                    v.created_by_system,
                    v.appointment_date,
                    v.appointment_notes_preview,
                )
                for v in visits
            },
        )


def test_real_schema_rerun_preserves_scope_and_existing_state(qa_practice):
    practice, doctors, _ = qa_practice
    before_practice = practice.model_dump()
    before_doctors = [doctor.model_dump() for doctor in doctors]
    manual_qa_seed.seed_manual_qa_fixtures(str(practice.id))
    first = _fixtures()
    manual_qa_seed.seed_manual_qa_fixtures(str(practice.id))
    assert _fixtures() == first
    patients, visits = first
    assert len(patients) == 2
    assert len(visits) == 4
    assert all(patients[visit[1]][0] == visit[0] for visit in visits.values())
    assert all(visit[3] == "manual_qa_seed" for visit in visits.values())
    practice.refresh()
    for doctor in doctors:
        doctor.refresh()
    assert practice.model_dump() == before_practice
    assert [doctor.model_dump() for doctor in doctors] == before_doctors


@pytest.mark.parametrize(
    "failure",
    [
        "baa",
        "subscription",
        "attestation",
        "disabled",
        "unowned",
        "unverified",
        "role",
        "onboarding",
        "exempt",
    ],
)
def test_real_access_denials_insert_nothing(qa_practice, failure):
    practice, doctors, identities = qa_practice
    doctor = doctors[1]
    if failure == "baa":
        practice.baa_accepted_at = None
        practice.save()
    elif failure == "subscription":
        practice.subscription_status = "canceled"
        practice.save()
    elif failure == "attestation":
        member = PracticeMember.get(doctor_id=doctor.id)
        assert member is not None
        member.attested_at = None
        save_practice_member(member)
    elif failure == "disabled":
        doctor.deleted_at = datetime.now(timezone.utc)
        doctor.save()
    elif failure == "unowned":
        identities[doctor.clerk_id].private_metadata = {}
    elif failure == "unverified":
        identities[doctor.clerk_id].email_addresses[
            0
        ].verification.status = "unverified"
    elif failure == "role":
        member = PracticeMember.get(doctor_id=doctor.id)
        assert member is not None
        member.role = "owner"
        save_practice_member(member)
    elif failure == "onboarding":
        doctor.onboarding_completed_at = None
        doctor.save()
    elif failure == "exempt":
        practice.billing_exempt = True
        practice.save()
    with pytest.raises(ValueError):
        manual_qa_seed.seed_manual_qa_fixtures(str(practice.id))
    assert _fixtures() == ({}, {})


def test_late_visit_conflict_rolls_back_both_actors(qa_practice):
    practice, doctors, _ = qa_practice
    unrelated = Patient(doctor_id=doctors[1].id, name="Other fictional patient").save()
    Appointment(
        doctor_id=doctors[1].id,
        patient_id=unrelated.id,
        external_id=f"{manual_qa_seed.QA_VISIT_KEY}:-7",
        created_by_system="unowned_test_fixture",
        appointment_date=datetime.now(),
        appointment_name="Other fictional visit",
    ).save()
    before = _fixtures()
    with pytest.raises(ValueError, match="different patient scope"):
        manual_qa_seed.seed_manual_qa_fixtures(str(practice.id))
    assert _fixtures() == before


def test_two_real_connections_serialize_reruns(qa_practice):
    practice, _, _ = qa_practice
    database_url = os.environ["TEST_DATABASE_URL"]
    with (
        ThreadPoolExecutor(max_workers=2) as executor,
        psycopg.connect(database_url) as blocker,
        psycopg.connect(database_url, autocommit=True) as observer,
    ):
        blocker.execute(
            "SELECT id FROM practice WHERE id = %s FOR UPDATE", (practice.id.uuid,)
        )
        futures = [
            executor.submit(manual_qa_seed.seed_manual_qa_fixtures, str(practice.id))
            for _ in range(2)
        ]
        deadline = time.monotonic() + 10
        while True:
            row = observer.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ).fetchone()
            assert row is not None
            if row[0] >= 2:
                break
            assert time.monotonic() < deadline, "seed calls did not queue on the lock"
            assert not any(future.done() for future in futures)
            time.sleep(0.02)
        blocker.rollback()
        for future in futures:
            future.result(timeout=10)
    patients, visits = _fixtures()
    assert len(patients) == 2
    assert len(visits) == 4
