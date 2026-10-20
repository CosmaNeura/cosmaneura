"""Exact capture lock evidence and final refresh; PG cases require owned fixtures."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any, cast

import pytest
from typeid import TypeID

from app.services import authority
from app.services import doctor_access_row_ownership as ownership
from app.services import scribe_capture_scope as capture

from activemodel.session_manager import _session_context
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from sqlalchemy import update
from sqlalchemy.dialects import postgresql
from sqlmodel import Session, select

from tests.authority_kernel.scribe_capture_scope_test import (
    _committed_case,
    _thread_resolve,
)
from tests.authority_kernel.scribe_capture_scope_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    query_counts,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

capture_engine, engine, native, workflow = _capture_engine, _engine, _native, _workflow


@pytest.fixture
def loaded_owner():
    # No connection or SQL: real mapped rows attached to a real Session are
    # enough to exercise the predicate's identity-map/transaction checks.
    with Session() as session:
        doctor = Doctor(clerk_id="user_synthetic_final_rows", biography=None)
        practice = Practice(
            legal_name="Invented final rows practice",
            join_code="synthetic-final",
            email_domain="synthetic.invalid",
        )
        # SQLModel assigns the generated identifiers at insert time; this fixture
        # deliberately compiles SQL without a database, so assign real IDs now.
        doctor.id = cast(TypeIDType, TypeID("doctor"))
        practice.id = cast(TypeIDType, TypeID("practice"))
        member = PracticeMember(
            doctor_id=doctor.id, practice_id=practice.id, role="provider"
        )
        session.add_all([doctor, practice, member])
        transaction = session.get_transaction()
        assert transaction is not None
        proof = ownership.DoctorAccessRowOwnership(
            session,
            transaction,
            doctor.id,
            practice.id,
            doctor.clerk_id,
            ownership._ISSUER,
            (doctor, practice, member),
        )
        scope = dict(
            doctor_id=doctor.id, practice_id=practice.id, subject=doctor.clerk_id
        )
        local = ownership._CAPTURE_ROWS.set(proof)
        context = _session_context.set(session)
        try:
            yield session, proof, scope
        finally:
            _session_context.reset(context)
            ownership._CAPTURE_ROWS.reset(local)


@pytest.mark.parametrize("different", ["doctor_id", "practice_id", "subject"])
def test_exact_owner_scope_required_without_sql(loaded_owner, different):
    session, _proof, scope = loaded_owner
    assert ownership.capture_owns_provider_rows(session, **scope)
    assert not ownership.capture_owns_provider_rows(
        session, **(scope | {different: "synthetic_other"})
    )
    with Session() as other:
        assert not ownership.capture_owns_provider_rows(other, **scope)


@pytest.mark.parametrize("invalid", ["issuer", "inactive", "rollback", "context"])
def test_unconfirmed_lock_evidence_refuses_without_sql(loaded_owner, invalid):
    session, proof, scope = loaded_owner
    if invalid == "issuer":
        ownership._CAPTURE_ROWS.set(replace(proof, _issuer=object()))
    elif invalid == "inactive":
        proof._invalidate()
    elif invalid == "rollback":
        session.rollback()
    else:
        _session_context.set(None)
    assert not ownership.capture_owns_provider_rows(session, **scope)


def test_join_locks_only_exact_already_owned_practice(loaded_owner):
    session, proof, scope = loaded_owner
    statements = []

    class Result:
        def one_or_none(self):
            return proof._rows[0], proof._rows[2], proof._rows[1]

    def execute(statement):
        statements.append(statement)
        return Result()

    # Compile the actual statement, without executing a database query.
    session.exec = execute
    # This predicate reads only the provider fields; mount identities stay unused.
    owner = capture._CaptureOwner(
        cast(TypeIDType, None),
        cast(TypeIDType, None),
        cast(TypeIDType, None),
        scope["subject"],
        scope["doctor_id"],
        scope["practice_id"],
    )
    assert capture._capture_final_provider_rows(session, owner) == (
        proof._rows[0],
        proof._rows[2],
        proof._rows[1],
    )
    assert len(statements) == 1
    statement = statements[0]
    sql = " ".join(str(statement.compile(dialect=postgresql.dialect())).split())
    assert "JOIN practice ON practice.id =" in sql
    assert "JOIN practice ON practice.id = practice_member.practice_id" not in sql
    assert "FOR UPDATE OF doctor, practice_member, practice" in sql
    assert statement.get_execution_options()["populate_existing"] is True


def test_real_native_join_keeps_receipt_and_removes_two_refresh_queries(
    native, engine, monkeypatch, record_property
):
    original = capture._capture_final_provider_rows
    counts = []
    replies = []

    def observed(session, owner):
        with query_counts(engine) as queries:
            result = original(session, owner)
        counts.append(queries["all_selects"])
        return result

    monkeypatch.setattr(capture, "_capture_final_provider_rows", observed)
    for joined in (False, True):
        counts.clear()
        with monkeypatch.context() as patch:
            if not joined:
                patch.setattr(
                    capture, "capture_owns_provider_rows", lambda *_a, **_k: False
                )
            response = native.post("creator_review")
        assert response.status_code == 200
        assert counts and counts == [1 if joined else 3] * len(counts)
        replies.append((response.json()["result"]["receipt"], len(counts)))
    assert replies[0] == replies[1]
    assert engine.pool.checkedout() == 0
    record_property("final_refresh_selects", "3->1 per eligible authorization")
    record_property("unchanged_complete_authorizations", replies[1][1])


@pytest.mark.parametrize("end", ["commit", "rollback", "savepoint"])
def test_actual_transaction_end_invalidates_join_evidence(capture_engine, end):
    case = _committed_case(capture_engine)
    with Session(capture_engine, expire_on_commit=False) as session:
        with ownership.capture_doctor_access_rows(
            session,
            doctor_id=case.doctor.id,
            practice_id=case.practice.id,
            subject=case.doctor.clerk_id,
        ) as held:
            # A transaction is required before the factory can issue evidence.
            assert held is None
        session.exec(select(Doctor).where(Doctor.id == case.doctor.id)).one()
        with ownership.capture_doctor_access_rows(
            session,
            doctor_id=case.doctor.id,
            practice_id=case.practice.id,
            subject=case.doctor.clerk_id,
        ) as held:
            assert held is not None
            scope = dict(
                doctor_id=case.doctor.id,
                practice_id=case.practice.id,
                subject=case.doctor.clerk_id,
            )
            assert ownership.capture_owns_provider_rows(session, **scope)
            if end == "savepoint":
                session.begin_nested().rollback()
            else:
                getattr(session, end)()
            assert not ownership.capture_owns_provider_rows(session, **scope)


@pytest.mark.parametrize("commit", [True, False])
def test_membership_wait_keeps_post_wait_revocation_and_rollback(
    capture_engine, monkeypatch, commit
):
    case = _committed_case(capture_engine)
    first = _thread_resolve(capture_engine, case.arguments)
    reached_refresh = Event()
    original = capture._capture_final_provider_rows

    def observe(session, owner):
        assert ownership.capture_owns_provider_rows(
            session,
            doctor_id=owner.doctor_id,
            practice_id=owner.practice_id,
            subject=owner.subject,
        )
        reached_refresh.set()
        return original(session, owner)

    monkeypatch.setattr(capture, "_capture_final_provider_rows", observe)
    with Session(capture_engine) as writer:
        member = writer.exec(
            select(PracticeMember)
            .where(PracticeMember.doctor_id == case.doctor.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(_thread_resolve, capture_engine, case.arguments)
            assert reached_refresh.wait(timeout=10)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.1)
            # An adversarial lower-level writer changes only the member row;
            # official offboarding's Practice->Doctor order remains unchanged.
            revoked_at = datetime.now(UTC)
            writer.execute(
                update(PracticeMember)
                .where(cast(Any, PracticeMember.id) == member.id)
                .values(
                    seat_state="inactive",
                    access_revoked_at=revoked_at,
                    access_revoked_by_ref="synthetic_reviewer",
                    access_revocation_reason="synthetic_revocation",
                    access_records_retain_until=revoked_at + timedelta(days=366 * 7),
                )
            )
            if commit:
                writer.commit()
                with pytest.raises(authority.AuthorityKernelError):
                    pending.result(timeout=10)
            else:
                writer.rollback()
                assert pending.result(timeout=10).binding_ref == first.binding_ref
    assert capture_engine.pool.checkedout() == 0
