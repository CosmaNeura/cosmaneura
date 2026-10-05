"""Actual owned PG queries; proof validity and original decision parity."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest

from app.services import doctor_access as access
from app.services import pilot_access_grandfather as pilot
from app.services.doctor_access_row_ownership import lock_doctor_access_rows

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from sqlalchemy import event, text
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import select

from tests.authority_kernel import doctor_access_previous_queries as previous
from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.doctor_access_query_equivalence_test import scenario
from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts

engine = _engine


def owned_case(engine, name="normal"):
    args, at = scenario(engine, name)
    with session_for(engine) as session:
        lock_practice_seats(session, args["practice_id"])
        member = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == args["doctor_id"])
        ).one_or_none()
        # _provider always has membership; cover pilot-grant variants in that
        # eligible shape. Unlocked/missing-member parity is tested separately.
        if member is None:
            session.add(
                PracticeMember(
                    practice_id=args["practice_id"],
                    doctor_id=args["doctor_id"],
                    role="workforce",
                    attested_at=at,
                )
            )
        session.commit()
    return args, at


def owner(session, args):
    return lock_doctor_access_rows(
        session,
        practice_id=args["practice_id"],
        doctor_id=args["doctor_id"],
        subject=args["verified_clerk_subject"],
    )


def decision(session, args, at, proof=None):
    return access.resolve_doctor_access(
        session.get(Doctor, args["doctor_id"]),
        at=at,
        _ownership=proof,
        _practice_id=args["practice_id"],
    )


@pytest.mark.parametrize(
    "name",
    [
        "normal",
        "pilot",
        "pilot_hash",
        "pilot_email",
        "pilot_doctor",
        "pilot_future",
        "pilot_unonboarded",
        "inactive",
        "revoked_member",
        "ending_before",
        "ending_at",
        "grant_revoked",
        "replacement",
        "baa_stale",
        "baa_future",
        "baa_effective",
        "no_entitlement",
        "exemption_before",
        "exemption_at",
    ],
)
@pytest.mark.parametrize("require_entitlement", [True, False])
def test_owned_complete_decision_and_cutoffs_match_original(
    engine, name, require_entitlement
):
    args, at = owned_case(engine, name)
    with session_for(engine) as session, owner(session, args) as locked:
        assert locked is not None
        doctor, proof = locked[1], locked[3]
        expected = previous.resolve_doctor_access(
            doctor, at=at, require_entitlement=require_entitlement
        )
        actual = access.resolve_doctor_access(
            doctor,
            at=at,
            require_entitlement=require_entitlement,
            _ownership=proof,
            _practice_id=args["practice_id"],
        )
        assert actual == expected


@pytest.mark.parametrize(
    "state",
    [
        None,
        "active",
        "trialing",
        "incomplete",
        "incomplete_expired",
        "past_due",
        "canceled",
        "unpaid",
        "paused",
        "pending_verification",
        "invalid",
        "invented_unknown",
    ],
)
@pytest.mark.parametrize("public", [False, True])
def test_owned_entitlement_matches_original(engine, state, public):
    args, at = owned_case(engine)
    with session_for(engine) as session:
        practice = session.get(Practice, args["practice_id"])
        assert practice is not None
        practice.billing_exempt = False
        practice.subscription_status = state
        practice.trial_ends_at = at + timedelta(seconds=1)
        practice.acquisition_source = "public_self_service" if public else "managed"
        session.add(practice)
        session.commit()
    with session_for(engine) as session, owner(session, args) as locked:
        assert locked is not None
        assert decision(session, args, at, locked[3]) == previous.resolve_doctor_access(
            locked[1], at=at
        )


@pytest.mark.parametrize("invalid", [False, True])
def test_owned_late_grant_validates_doctor_before_cardinality(engine, invalid):
    args, at = owned_case(engine, "duplicate")
    with session_for(engine) as session:
        if invalid:
            doctor = session.get(Doctor, args["doctor_id"])
            assert doctor is not None
            doctor.email = "invalid-" + doctor.clerk_id
            session.add(doctor)
            session.commit()
        with owner(session, args) as locked:
            assert locked is not None
            doctor, proof = locked[1], locked[3]
            for function, options in [
                (previous.active_pilot_access_grant, {}),
                (pilot.active_pilot_access_grant, {"_ownership": proof}),
            ]:
                if invalid:
                    assert function(doctor, at=at, **options) is None
                else:
                    with pytest.raises(MultipleResultsFound):
                        function(doctor, at=at, **options)


def test_owned_lookup_preserves_loaded_identity_map(engine):
    args, at = owned_case(engine, "pilot")
    with session_for(engine) as session, owner(session, args) as locked:
        assert locked is not None
        doctor, proof = locked[1], locked[3]
        set_committed_value(doctor, "email", "different@synthetic.invalid")
        assert previous.active_pilot_access_grant(doctor, at=at) is None
        assert pilot.active_pilot_access_grant(doctor, at=at, _ownership=proof) is None
        session.expire(doctor, ["email"])
        assert previous.active_pilot_access_grant(doctor, at=at) is not None
        assert (
            pilot.active_pilot_access_grant(doctor, at=at, _ownership=proof) is not None
        )


def test_changed_loaded_membership_identity_falls_back_without_implicit_proof_sql(
    engine,
):
    args, at = owned_case(engine)
    other, _ = owned_case(engine)
    with session_for(engine) as session, owner(session, args) as locked:
        assert locked is not None
        doctor, member, proof = locked[1], locked[2], locked[3]
        set_committed_value(member, "practice_id", other["practice_id"])
        with query_counts(engine) as proof_counts:
            assert not proof.matches(session, doctor, practice_id=args["practice_id"])
        assert proof_counts["all_selects"] == 0
        expected = previous.resolve_doctor_access(doctor, at=at)
        with query_counts(engine) as counts:
            actual = decision(session, args, at, proof)
        assert actual == expected and counts["all_selects"] == 6


@pytest.mark.parametrize("expired", ["doctor", "membership"])
def test_expired_owner_rows_cannot_trigger_sql_during_proof_validation(engine, expired):
    args, _at = owned_case(engine)
    with session_for(engine) as session, owner(session, args) as locked:
        assert locked is not None
        doctor, member, proof = locked[1], locked[2], locked[3]
        session.expire(doctor if expired == "doctor" else member)
        with query_counts(engine) as counts:
            assert not proof.matches(session, doctor, practice_id=args["practice_id"])
        assert counts["all_selects"] == 0


@pytest.mark.parametrize(
    "invalid",
    [
        "absent",
        "untyped",
        "issuer",
        "doctor",
        "practice",
        "subject",
        "session",
        "after_scope",
        "commit",
        "rollback",
        "savepoint_release",
        "savepoint_rollback",
        "link_practice",
        "link_doctor",
    ],
)
def test_invalid_or_reused_proof_uses_all_original_queries(engine, invalid):
    args, at = owned_case(engine)
    other, _ = owned_case(engine)
    with session_for(engine) as session:
        with owner(session, args) as locked:
            assert locked is not None
            proof = locked[3]
            if invalid == "absent":
                proof = None
            elif invalid == "untyped":
                proof = object()
            elif invalid in {"issuer", "doctor", "practice", "subject"}:
                proof = replace(
                    proof,
                    **{
                        "issuer": {"_issuer": object()},
                        "doctor": {"_doctor_id": other["doctor_id"]},
                        "practice": {"_practice_id": other["practice_id"]},
                        "subject": {"_subject": other["verified_clerk_subject"]},
                    }[invalid],
                )
            elif invalid == "commit":
                session.commit()
            elif invalid == "rollback":
                session.rollback()
            elif invalid.startswith("savepoint"):
                savepoint = session.begin_nested()
                (
                    savepoint.commit
                    if invalid.endswith("release")
                    else savepoint.rollback
                )()
            if invalid == "after_scope":
                pass
            elif invalid == "session":
                with session_for(engine) as another:
                    held = another.get(Doctor, args["doctor_id"])
                    assert held is not None
                    with query_counts(engine) as counts:
                        assert decision(another, args, at, proof).allowed
                    assert counts["all_selects"] == 6
                return
            elif invalid in {"link_practice", "link_doctor"}:
                linked = dict(args)
                linked[invalid.removeprefix("link_") + "_id"] = other[
                    invalid.removeprefix("link_") + "_id"
                ]
                held = session.get(Doctor, linked["doctor_id"])
                assert held is not None
                with query_counts(engine) as counts:
                    assert decision(session, linked, at, proof).allowed
                assert counts["all_selects"] == 6
                return
            else:
                session.get(Doctor, args["doctor_id"])
                with query_counts(engine) as counts:
                    assert decision(session, args, at, proof).allowed
                assert counts["all_selects"] == 6
                return
        with query_counts(engine) as counts:
            assert decision(session, args, at, proof).allowed
        assert counts["all_selects"] == 6


def test_valid_proof_has_three_fresh_queries_and_default_clock_stays_at_same_stage(
    engine, monkeypatch
):
    args, at = owned_case(engine)
    order = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            order.append("clock")
            return at

    def selected(_connection, _cursor, statement, *_args):
        shape = " ".join(statement.lower().split())
        if shape.startswith("select"):
            order.append("member" if " from practice_member " in shape else "other")

    monkeypatch.setattr(access, "datetime", Clock)
    with session_for(engine) as session, owner(session, args) as locked:
        event.listen(engine, "before_cursor_execute", selected)
        try:
            with query_counts(engine) as counts:
                assert locked is not None
                assert decision(session, args, None, locked[3]).allowed
        finally:
            event.remove(engine, "before_cursor_execute", selected)
        assert counts["all_selects"] == 3
        assert order == ["other", "member", "clock", "other"]


@pytest.mark.parametrize(
    "mismatch", ["practice", "doctor", "subject", "disabled", "membership"]
)
def test_factory_keeps_original_post_wait_denial(engine, mismatch):
    args, at = owned_case(engine)
    other, _ = owned_case(engine)
    with session_for(engine) as session:
        kwargs = dict(
            practice_id=args["practice_id"],
            doctor_id=args["doctor_id"],
            subject=args["verified_clerk_subject"],
        )
        if mismatch in {"practice", "doctor"}:
            kwargs[mismatch + "_id"] = other[mismatch + "_id"]
        elif mismatch == "subject":
            kwargs["subject"] = other["verified_clerk_subject"]
        else:
            doctor = session.get(Doctor, args["doctor_id"])
            if mismatch == "disabled":
                assert doctor is not None
                doctor.deleted_at = at
                session.add(doctor)
            else:
                lock_practice_seats(session, args["practice_id"])
                assert doctor is not None
                session.delete(
                    session.exec(
                        select(PracticeMember).where(
                            PracticeMember.doctor_id == doctor.id
                        )
                    ).one()
                )
            session.commit()
        with lock_doctor_access_rows(session, **kwargs) as locked:
            assert locked is None


@pytest.mark.parametrize("change", ["subject", "membership", "disabled"])
def test_factory_rechecks_owner_after_waiting_for_actual_row_lock(engine, change):
    args, at = owned_case(engine)
    other, _ = owned_case(engine)
    attempted = Event()

    def waiting(_connection, _cursor, statement, *_args):
        if (
            "from practice " in " ".join(statement.lower().split())
            and "FOR UPDATE" in statement
        ):
            attempted.set()

    def reader():
        with session_for(engine) as session:
            session.exec(text("SET LOCAL lock_timeout = '2s'"))
            with owner(session, args) as locked:
                return locked is None

    with session_for(engine) as writer:
        lock_practice_seats(writer, args["practice_id"])
        lock_practice_seats(writer, other["practice_id"])
        doctor = writer.exec(
            select(Doctor).where(Doctor.id == args["doctor_id"]).with_for_update()
        ).one()
        event.listen(engine, "before_cursor_execute", waiting)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(reader)
                assert attempted.wait(2)
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.05)
                if change == "subject":
                    doctor.clerk_id = "user_changed_" + uuid4().hex
                    writer.add(doctor)
                elif change == "disabled":
                    doctor.deleted_at = at
                    writer.add(doctor)
                else:
                    member = writer.exec(
                        select(PracticeMember).where(
                            PracticeMember.doctor_id == doctor.id
                        )
                    ).one()
                    member.practice_id = other["practice_id"]
                    writer.add(member)
                writer.commit()
                assert future.result(timeout=3)
        finally:
            event.remove(engine, "before_cursor_execute", waiting)
