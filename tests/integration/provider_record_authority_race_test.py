"""Real independent-connection races; no browser, IdP, or external model."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from queue import Queue
from threading import Barrier
from time import monotonic, sleep

import app.configuration.session_prep as configuration
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services.authority import AuthorityRequestDeniedError, exchange_context_token
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    note_content_sha256,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlalchemy import text
from sqlmodel import select

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory
from tests.routes.utils import provision_practice_in_force
from tests.services.session_prep_authority_test import AUTHORITY_SETTINGS, ISSUER
from tests.services.session_prep_note_sources_test import _note


def test_concurrent_exchanges_create_one_record_and_practice_scope(monkeypatch):
    doctor = DoctorFactory.build().save()
    provision_practice_in_force(doctor)
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True, frozenset({doctor.clerk_id})),
    )
    barrier = Barrier(2)

    def exchange():
        with get_session() as session:
            session.exec(text("SET LOCAL lock_timeout = '3s'"))
            session.exec(text("SET LOCAL statement_timeout = '5s'"))
            barrier.wait(timeout=5)
            return exchange_context_token(
                session=session,
                clerk_issuer=ISSUER,
                clerk_subject=doctor.clerk_id,
                requested_surface="provider_assistant",
                record_ref=str(appointment.id),
                settings=AUTHORITY_SETTINGS,
            ).context_token

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(exchange) for _ in range(2)]
        tokens = [future.result(timeout=10) for future in futures]
    assert len(set(tokens)) == 2
    assert ProviderRecordAuthority.count() == 1
    assert ProviderPracticeAuthority.count() == 1


def test_concurrent_review_actions_record_only_one_exact_revision():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    note = _note(doctor, patient)
    digest = note_content_sha256(note.note)
    barrier = Barrier(2)

    def review():
        with get_session() as session:
            session.exec(text("SET LOCAL lock_timeout = '3s'"))
            session.exec(text("SET LOCAL statement_timeout = '5s'"))
            barrier.wait(timeout=5)
            record = finalize_note_for_session_prep(
                session=session,
                doctor_id=doctor.id,
                note_id=note.id,
                expected_content_sha256=digest,
                observed_at=datetime.now(UTC),
            )
            result = str(record.id)
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(review) for _ in range(2)]
        receipts = [future.result(timeout=10) for future in futures]
    assert len(set(receipts)) == 1
    assert SessionPrepNoteFinalization.count() == 1


def test_offboarding_can_lock_doctor_while_exchange_waits_for_practice(monkeypatch):
    doctor = DoctorFactory.build().save()
    practice = provision_practice_in_force(doctor)
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True, frozenset({doctor.clerk_id})),
    )
    pids: Queue[int] = Queue()

    def exchange():
        with get_session() as session:
            session.exec(text("SET LOCAL statement_timeout = '5s'"))
            pids.put(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            try:
                exchange_context_token(
                    session=session,
                    clerk_issuer=ISSUER,
                    clerk_subject=doctor.clerk_id,
                    requested_surface="provider_assistant",
                    record_ref=str(appointment.id),
                    settings=AUTHORITY_SETTINGS,
                )
            except AuthorityRequestDeniedError:
                session.rollback()
                return "denied"
            return "accepted"

    with ThreadPoolExecutor(max_workers=1) as workers, get_session() as session:
        session.exec(
            select(Practice).where(Practice.id == practice.id).with_for_update()
        ).one()
        future = workers.submit(exchange)
        try:
            pid = pids.get(timeout=3)
            deadline = monotonic() + 3
            while True:
                session.exec(text("SELECT pg_stat_clear_snapshot()"))
                wait_type = session.exec(
                    text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                    ),
                    params={"pid": pid},
                ).scalar_one()
                if wait_type == "Lock":
                    break
                assert monotonic() < deadline, (
                    "Exchange did not reach the practice lock"
                )
                sleep(0.02)
            # This is the organization's established Practice -> Doctor order.
            # Doctor-first exchange would already hold this row and fail NOWAIT.
            session.exec(
                select(Doctor)
                .where(Doctor.id == doctor.id)
                .with_for_update(nowait=True)
            ).one()
            member = session.exec(
                select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
            ).one()
            member.seat_state = "inactive"
            session.add(member)
            session.commit()
            assert future.result(timeout=5) == "denied"
        finally:
            session.rollback()
    assert ProviderRecordAuthority.count() == 0
