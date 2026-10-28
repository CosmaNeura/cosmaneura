"""Validity across real lineage lock contention, using only owned synthetic rows.

Run with --confcutdir=tests/authority_kernel and an explicitly allowed disposable
W8_CORE_CAPTURE_TEST_URL. No repository-wide reset, Clerk, browser or model.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.configuration.session_prep as configuration
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services import authority
from app.services.authority_conversation import write_conversation_turn
from app.services.provider_record_authority import validate_provider_record_scope

from app.models.appointment import Appointment
from app.models.authority import (
    InteractionLineage,
    MountContextSession,
    MountContextToken,
)
from app.models.authority_conversation import (
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from sqlalchemy import text
from sqlmodel import Session, col, select

from tests.authority_kernel.scribe_capture_scope_test import (
    SETTINGS,
)
from tests.authority_kernel.scribe_capture_scope_test import (
    capture_engine as _capture_engine,
)

capture_engine = _capture_engine
WORKLOAD = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def owned_record(capture_engine, monkeypatch):
    """Fresh unique provider/practice/record; never truncate another test's rows."""
    monkeypatch.setattr(
        authority, "validate_provider_record_scope", validate_provider_record_scope
    )
    now = datetime.now(UTC)
    with Session(capture_engine, expire_on_commit=False) as session:
        doctor = Doctor(clerk_id=f"user_expiry_{uuid4().hex}", biography=None)
        practice = Practice(
            legal_name="Synthetic expiry practice",
            email_domain="expiry.invalid",
            join_code=uuid4().hex[:16],
            baa_accepted_at=now,
            baa_version=BAA_CURRENT_REFERENCE,
            billing_exempt=True,
        )
        session.add_all([doctor, practice])
        session.flush()
        patient = Patient(
            doctor_id=doctor.id,
            name="Synthetic Expiry Patient",
            date_of_birth=datetime(1990, 1, 1, tzinfo=UTC),
        )
        session.add(patient)
        session.flush()
        appointment = Appointment(
            doctor_id=doctor.id,
            patient_id=patient.id,
            appointment_date=now,
            external_id=None,
        )
        assert lock_practice_seats(session, practice.id) is not None
        session.add_all(
            [
                appointment,
                PracticeMember(
                    practice_id=practice.id,
                    doctor_id=doctor.id,
                    role="owner",
                    attested_at=now,
                ),
            ]
        )
        session.commit()
        monkeypatch.setattr(
            configuration,
            "SESSION_PREP_PROVIDER_SETTINGS",
            SessionPrepProviderSettings(True),
        )
        token = authority.exchange_context_token(
            session=session,
            clerk_issuer=SETTINGS.clerk_issuer,
            clerk_subject=doctor.clerk_id,
            requested_surface="provider_assistant",
            record_ref=str(appointment.id),
            settings=SETTINGS,
        ).context_token
        turn_id = uuid4()
        resolution = authority.resolve_mount_context(
            session=session,
            workload_client_id=WORKLOAD,
            mount_context_token=token,
            client_turn_id=turn_id,
            settings=SETTINGS,
        )
        assert resolution.context.session_prep_context_ref is not None
        yield SimpleNamespace(
            engine=capture_engine,
            token=token,
            mount_id=resolution.context.session_id,
            turn_id=turn_id,
            interaction_ref=resolution.interaction_ref,
        )


def _await_lineage_block(engine, waiting_pid, blocking_pid):
    deadline = monotonic() + 3
    with engine.connect() as connection:
        while monotonic() < deadline:
            blockers = connection.execute(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting_pid}
            ).scalar_one()
            if blocking_pid in blockers:
                return
            sleep(0.01)
    pytest.fail("The writer did not wait on the held lineage row")


@pytest.mark.parametrize(
    "change", ["token_expiry", "family_expiry", "revoked", "valid"]
)
def test_canonical_write_rechecks_validity_after_real_lineage_wait(
    owned_record, change
):
    f = owned_record
    assert f.engine.dialect.name == "postgresql"
    pids = Queue()

    def write():
        with Session(f.engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '6s'"))
            session.execute(text("SET LOCAL statement_timeout = '8s'"))
            pids.put(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            try:
                result = write_conversation_turn(
                    session=session,
                    workload_client_id=WORKLOAD,
                    mount_context_token=f.token,
                    interaction_ref=f.interaction_ref,
                    client_turn_id=f.turn_id,
                    idempotency_key=f"expiry-{uuid4().hex}",
                    payload={
                        "phase": "user_accepted",
                        "ingress_kind": "chat_text",
                        "action_code": None,
                        "message": {
                            "content_type": "text/plain",
                            "content": "Summarize this synthetic record.",
                        },
                    },
                    settings=SETTINGS,
                )
                return result.state
            except (
                authority.ContextTokenExpiredError,
                authority.ContextTokenInvalidError,
            ) as error:
                return type(error).__name__

    with Session(f.engine) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker_pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
        lineage = blocker.exec(
            select(InteractionLineage)
            .where(InteractionLineage.interaction_ref == f.interaction_ref)
            .with_for_update()
        ).one()
        lineage_id = lineage.id
        deadline = datetime.now(UTC) + timedelta(seconds=2)
        if change in {"token_expiry", "family_expiry"}:
            with Session(f.engine) as setup:
                if change == "token_expiry":
                    row = setup.exec(
                        select(MountContextToken).where(
                            MountContextToken.session_id == f.mount_id
                        )
                    ).one()
                    row.expires_at = deadline
                else:
                    row = setup.get(MountContextSession, f.mount_id)
                    assert row is not None
                    row.absolute_expires_at = deadline
                setup.add(row)
                setup.commit()
        future = pool.submit(write)
        try:
            _await_lineage_block(f.engine, pids.get(timeout=3), blocker_pid)
            assert not future.done()
            if change in {"token_expiry", "family_expiry"}:
                assert datetime.now(UTC) < deadline, (
                    "Did not establish contention before expiry"
                )
                while datetime.now(UTC) <= deadline:
                    sleep(0.01)
            elif change == "revoked":
                # The token is locked by the writer; the independent family
                # row can be revoked while its old ORM object is cached.
                with Session(f.engine) as revoker:
                    mount = revoker.get(MountContextSession, f.mount_id)
                    assert mount is not None
                    mount.status = "revoked"
                    mount.revoked_at = datetime.now(UTC)
                    revoker.add(mount)
                    revoker.commit()
        finally:
            blocker.rollback()
        outcome = future.result(timeout=5)
    expected = {
        "token_expiry": "ContextTokenExpiredError",
        "family_expiry": "ContextTokenExpiredError",
        "revoked": "ContextTokenInvalidError",
        "valid": "accepted",
    }
    assert outcome == expected[change]
    with Session(f.engine) as session:
        phases = session.exec(
            select(CanonicalConversationUserPhase)
            .join(
                CanonicalConversationTurn,
                col(CanonicalConversationTurn.id)
                == col(CanonicalConversationUserPhase.turn_id),
            )
            .where(CanonicalConversationTurn.interaction_id == lineage_id)
        ).all()
        assert len(phases) == (1 if change == "valid" else 0)
