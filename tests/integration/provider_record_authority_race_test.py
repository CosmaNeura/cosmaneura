"""Real independent-connection races; no browser, IdP, or external model."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import app.configuration.session_prep as configuration
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services.authority import exchange_context_token
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    note_content_sha256,
)

from activemodel.session_manager import get_session
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlalchemy import text

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
