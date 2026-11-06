"""Own-member acknowledgement repair on an explicitly owned synthetic PostgreSQL.

Run with --confcutdir=tests/authority_kernel; never load the root test hooks.
"""

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routes import internal
from app.services.scribe_activation import ActivationDenied

from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.scribe_cancellation import scribe_cancellation_operation
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.scribe_activation_test import activate, grant
from tests.authority_kernel.scribe_activation_test import (
    snapshot as activation_snapshot,
)


@pytest.fixture(scope="module")
def engine():
    raw = os.environ["SELF_ATTESTATION_TEST_DATABASE_URL"]
    url = make_url(raw)
    assert (url.host, url.port, url.database, url.username) == (
        None,
        55485,
        "cn_self_reattest_demo_20261106",
        "jw",
    )
    assert url.query == {"host": "/private/tmp/cn-demo-followup-pg-20261106"}
    for source in (Path(__file__).parents[2] / "app/models").glob("*.py"):
        if source.stem != "__init__":
            importlib.import_module("app.models." + source.stem)
    result = create_engine(url, connect_args={"connect_timeout": 3})
    with result.connect() as connection:
        assert connection.execute(
            text("SELECT current_database(), current_user, inet_server_addr()")
        ).one() == ("cn_self_reattest_demo_20261106", "jw", None)
    SQLModel.metadata.create_all(result)
    yield result
    result.dispose()


@pytest.fixture
def member_case(engine, monkeypatch):
    suffix = uuid4().hex
    domain = suffix + ".synthetic.invalid"
    now = datetime.now(UTC)
    with session_for(engine) as session:
        doctor = Doctor(
            clerk_id="user_self_attest_" + suffix,
            email="clinician@" + domain,
            biography=None,
            last_active_at=now,
            onboarding_completed_at=now,
        )
        practice = Practice(
            legal_name="Invented current practice",
            email_domain=domain,
            join_code=suffix[:16].upper(),
            is_solo=False,
            baa_version=BAA_CURRENT_REFERENCE,
            baa_accepted_at=now,
            billing_exempt=True,
        )
        session.add_all([doctor, practice])
        session.flush()
        lock_practice_seats(session, practice.id)
        member = PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
            attestation_name="Historical incomplete name",
        )
        session.add(member)
        session.commit()
        f = SimpleNamespace(
            engine=engine,
            doctor_id=doctor.id,
            subject=doctor.clerk_id,
            email=doctor.email,
            practice_id=practice.id,
            member_id=member.id,
            code=practice.join_code,
            calls=[],
        )
    monkeypatch.setattr(internal, "verified_primary_email", lambda subject: f.email)
    monkeypatch.setattr(internal, "_consume_legacy_join_attempt", lambda doctor: None)

    def committed_activation(**kwargs):
        with Session(engine) as check:
            row = check.get(PracticeMember, f.member_id)
            assert row is not None and row.attested_at is not None
        f.calls.append(kwargs)

    monkeypatch.setattr(internal, "activate_scribe_after_commit", committed_activation)
    return f


def submit(f, *, join=False, **changes):
    with session_for(f.engine) as session:
        doctor = session.get(Doctor, f.doctor_id)
        request = SimpleNamespace(state=SimpleNamespace(doctor=doctor, view_as=None))
        body = {
            "attestation_name": "  Synthetic Clinician  ",
            "checks": ["workforce", "not-party", "direction", "phi"],
        }
        if join:
            return internal.join_practice(
                request,
                internal.JoinPracticeBody(**(body | {"join_code": f.code} | changes)),
            )
        return internal.attest_current_practice_member(
            request,
            internal.CurrentPracticeAttestationBody(
                **(
                    body
                    | {
                        "practice_ref": str(f.practice_id),
                        "baa_reference": BAA_CURRENT_REFERENCE,
                    }
                    | changes
                )
            ),
        )


def snapshot(f):
    with Session(f.engine) as session:
        member = session.get(PracticeMember, f.member_id)
        practice = session.get(Practice, f.practice_id)
        assert member is not None and practice is not None
        return member.model_dump(), practice.model_dump()


@pytest.mark.parametrize("join", [False, True])
def test_real_acks_repair_only_attestation_after_commit(member_case, join):
    f = member_case
    before_member, before_practice = snapshot(f)
    result = submit(f, join=join)
    after_member, after_practice = snapshot(f)
    assert result.attested and result.authorization_basis == "workforce_attestation"
    assert not result.self_attestation_available
    assert after_member["attestation_name"] == "Synthetic Clinician"
    assert after_member["attested_at"] is not None
    assert {
        key for key in before_member if before_member[key] != after_member[key]
    } <= {"attested_at", "attestation_name", "updated_at"}
    assert after_practice == before_practice
    assert f.calls == [{"trigger": "membership", "doctor_ref": f.doctor_id}]


def test_repeat_preserves_original_legal_record(member_case):
    f = member_case
    submit(f)
    before = snapshot(f)
    result = submit(f, attestation_name="Different synthetic name")
    assert result.attested
    assert snapshot(f) == before
    assert len(f.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"checks": ["workforce", "not-party", "direction"]},
        {"attestation_name": "   "},
        {"practice_ref": "practice_" + "0" * 26},
        {"baa_reference": "Unreviewed agreement"},
    ],
)
def test_incomplete_or_other_practice_submission_writes_nothing(member_case, changes):
    f = member_case
    before = snapshot(f)
    with pytest.raises(HTTPException):
        submit(f, **changes)
    assert snapshot(f) == before
    assert not f.calls


@pytest.mark.parametrize(
    "changed", ["inactive", "ending", "revoked", "deleted", "baa", "domain", "managed"]
)
def test_live_ineligibility_cannot_be_repaired_by_acks(member_case, changed):
    f = member_case
    with session_for(f.engine) as session:
        member = session.get(PracticeMember, f.member_id)
        doctor = session.get(Doctor, f.doctor_id)
        practice = session.get(Practice, f.practice_id)
        assert member is not None and doctor is not None and practice is not None
        lock_practice_seats(session, practice.id)
        if changed in {"inactive", "ending"}:
            member.seat_state = "inactive"
            if changed == "ending":
                member.access_ends_at = datetime.now(UTC) + timedelta(days=1)
        elif changed == "revoked":
            member.access_revoked_at = datetime.now(UTC)
            member.access_revoked_by_ref = "synthetic_operator"
            member.access_revocation_reason = "synthetic test"
            member.access_records_retain_until = datetime.now(UTC) + timedelta(
                days=365 * 8
            )
        elif changed == "deleted":
            doctor.deleted_at = datetime.now(UTC)
        elif changed == "baa":
            practice.baa_accepted_at = None
        elif changed == "domain":
            practice.email_domain = "another.synthetic.invalid"
        elif changed == "managed":
            practice.baa_version = None
            practice.baa_accepted_at = None
            practice.negotiated_baa_required = True
        session.commit()
    before = snapshot(f)
    with pytest.raises(HTTPException):
        submit(f)
    assert snapshot(f) == before
    assert not f.calls


def test_live_verified_identity_is_mandatory(member_case, monkeypatch):
    f = member_case
    before = snapshot(f)
    monkeypatch.setattr(internal, "verified_primary_email", lambda subject: None)
    with pytest.raises(HTTPException) as failure:
        submit(f)
    assert failure.value.status_code == 403
    assert snapshot(f) == before


def test_request_cannot_supply_another_doctor_or_membership(member_case):
    f = member_case
    before = snapshot(f)
    with pytest.raises(ValidationError):
        internal.CurrentPracticeAttestationBody(
            practice_ref=str(f.practice_id),
            baa_reference=BAA_CURRENT_REFERENCE,
            attestation_name="Synthetic Clinician",
            checks=["workforce", "not-party", "direction", "phi"],
            doctor_ref=str(f.doctor_id),
        )
    assert snapshot(f) == before


def test_view_as_cannot_record_another_members_attestation(member_case):
    f = member_case
    before = snapshot(f)
    with session_for(f.engine) as session:
        doctor = session.get(Doctor, f.doctor_id)
        member = session.get(PracticeMember, f.member_id)
        practice = session.get(Practice, f.practice_id)
        assert doctor is not None and member is not None and practice is not None
        request = SimpleNamespace(
            state=SimpleNamespace(doctor=doctor, view_as=object())
        )
        with pytest.raises(HTTPException) as failure:
            internal._attest_current_practice_member(
                request,
                internal.CurrentPracticeAttestationBody(
                    practice_ref=str(f.practice_id),
                    baa_reference=BAA_CURRENT_REFERENCE,
                    attestation_name="Synthetic Clinician",
                    checks=["workforce", "not-party", "direction", "phi"],
                ),
                practice,
                member,
            )
    assert failure.value.status_code == 403
    assert snapshot(f) == before


def test_status_is_advisory_and_never_exposes_another_practice_code(member_case):
    f = member_case
    with session_for(f.engine) as session:
        member = session.get(PracticeMember, f.member_id)
        practice = session.get(Practice, f.practice_id)
        assert member is not None and practice is not None
        practice.is_solo = True
        session.commit()
        own = internal._practice_payload(practice, member)
        view_as = internal._practice_payload(practice, member, include_join_code=False)
    assert own is not None and own.self_attestation_available and own.join_code is None
    assert own.attestation_practice_ref == str(f.practice_id)
    assert own.attestation_baa_reference == BAA_CURRENT_REFERENCE
    assert view_as is not None and not view_as.self_attestation_available
    assert view_as.attestation_practice_ref is None
    assert view_as.attestation_baa_reference is None
    result = submit(f)
    assert result.attested and result.join_code is None


def test_locked_read_refuses_a_moved_or_replaced_membership(member_case, monkeypatch):
    f = member_case
    original = internal._membership_for

    def membership_then_move(doctor):
        old = original(doctor)
        assert old is not None
        with session_for(f.engine) as other:
            practice = Practice(
                legal_name="Invented moved practice",
                email_domain="synthetic.invalid",
                join_code=uuid4().hex[:16],
            )
            other.add(practice)
            other.flush()
            lock_practice_seats(other, f.practice_id)
            lock_practice_seats(other, practice.id)
            member = other.get(PracticeMember, f.member_id)
            assert member is not None
            member.practice_id = practice.id
            other.commit()
        return old

    monkeypatch.setattr(internal, "_membership_for", membership_then_move)
    with pytest.raises(HTTPException):
        submit(f)
    with Session(f.engine) as session:
        member = session.get(PracticeMember, f.member_id)
        assert member is not None and member.attested_at is None
    assert not f.calls


@pytest.mark.parametrize("field", ["email", "clerk_id"])
def test_fresh_locked_identity_refuses_concurrent_rebinding(
    member_case, monkeypatch, field
):
    f = member_case
    original = internal.lock_roster_enrollment_email

    def lock_then_rebind(session, email):
        original(session, email)
        with Session(f.engine) as other:
            doctor = other.get(Doctor, f.doctor_id)
            assert doctor is not None
            setattr(
                doctor,
                field,
                "changed-" + uuid4().hex + "@synthetic.invalid"
                if field == "email"
                else "user_changed_" + uuid4().hex,
            )
            other.commit()

    monkeypatch.setattr(internal, "lock_roster_enrollment_email", lock_then_rebind)
    with pytest.raises(HTTPException) as failure:
        submit(f)
    assert failure.value.status_code == 409
    member, _ = snapshot(f)
    assert member["attested_at"] is None
    assert not f.calls


def test_concurrent_real_submissions_record_once_under_ordered_locks(member_case):
    f = member_case
    barrier = Barrier(2)
    captured = []

    def capture(connection, cursor, statement, params, context, many):
        if "FOR UPDATE" in statement:
            captured.append(statement)

    def run(name):
        barrier.wait(timeout=5)
        return submit(f, attestation_name=name)

    event.listen(f.engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ["Synthetic One", "Synthetic Two"]))
    finally:
        event.remove(f.engine, "before_cursor_execute", capture)
    assert all(result.attested for result in results)
    assert len(f.calls) == 1
    member, _ = snapshot(f)
    assert member["attestation_name"] in {"Synthetic One", "Synthetic Two"}
    assert any("FROM practice " in sql for sql in captured)
    assert any("FROM doctor " in sql for sql in captured)
    assert any("FROM practice_member " in sql for sql in captured)


@pytest.mark.parametrize("block", [None, "cancelled", "revoked-grant"])
def test_recorded_basis_keeps_existing_scribe_tombstone_fences(member_case, block):
    f = member_case
    with session_for(f.engine) as session:
        doctor = session.get(Doctor, f.doctor_id)
        assert doctor is not None
        if block == "revoked-grant":
            grant(session, doctor, f.practice_id, revoked=True)
        elif block == "cancelled":
            now = datetime.now(UTC)
            session.execute(
                scribe_cancellation_operation.insert().values(
                    id=uuid4(),
                    caller_sha256=uuid4().hex * 2,
                    doctor_ref=str(doctor.id),
                    request_key_sha256="a" * 64,
                    preview_sha256="b" * 64,
                    inventory_sha256="c" * 64,
                    inventory=[],
                    policy={
                        "grace_days": 90,
                        "phi_scope": "scribe-processing-copies",
                        "approval_sha256": "d" * 64,
                    },
                    accepted_at=now,
                    export_until=now + timedelta(days=90),
                    phase="read-only",
                )
            )
        session.commit()
    submit(f)
    args = {
        "practice_id": f.practice_id,
        "verified_clerk_subject": f.subject,
    }
    before = activation_snapshot(f.engine)
    if block is not None:
        with pytest.raises(ActivationDenied):
            activate(f.engine, args)
        assert activation_snapshot(f.engine) == before
    else:
        assert activate(f.engine, args).state == "active"
