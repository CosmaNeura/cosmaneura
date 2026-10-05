"""Committed-state and real SQL race comparison; all records are invented."""

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.services import doctor_access as access
from app.services import pilot_access_grandfather as pilot

from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from sqlalchemy import event
from sqlalchemy.exc import MultipleResultsFound
from sqlmodel import Session, select

from tests.authority_kernel import doctor_access_previous_queries as previous
from tests.authority_kernel.companion_provider_provisioning_test import (
    case,
    session_for,
)
from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts

engine = _engine


@contextmanager
def old_queries(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            access, "active_pilot_access_grant", previous.active_pilot_access_grant
        )
        patch.setattr(
            access, "revoked_pilot_access_grant", previous.revoked_pilot_access_grant
        )
        patch.setattr(access, "resolve_doctor_access", previous.resolve_doctor_access)
        yield


def grant(session, doctor, practice, at, *, revoked=None, **changes):
    email = doctor.email.lower().strip()
    row = PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref="synthetic-query-equivalence",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="synthetic-local-test",
        grant_reason="Synthetic query comparison",
        granted_at=at - timedelta(days=1),
    )
    if revoked is not None:
        revoke(row, revoked)
    for key, value in changes.items():
        setattr(row, key, value)
    session.add(row)
    return row


def revoke(row, at):
    row.revoked_at = at
    row.revoked_by_ref = "synthetic-local-test"
    row.revocation_reason = "Synthetic concurrent revocation"
    row.revocation_plan_sha256 = "c" * 64
    row.retain_until = at + timedelta(days=366 * 8)


def scenario(engine, name):
    args = case(engine)
    at = datetime.now(UTC)
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        practice = session.get(Practice, args["practice_id"])
        assert practice is not None
        lock_practice_seats(session, practice.id)
        assert doctor is not None
        member = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
        ).one()
        member.role = "workforce"
        if name.startswith("pilot") or name in {
            "disabled",
            "inactive",
            "revoked_member",
            "ending_before",
            "ending_at",
            "grant_revoked",
            "replacement",
            "duplicate",
        }:
            doctor.onboarding_completed_at = at - timedelta(days=1)
            changes = {}
            if name == "pilot_hash":
                changes["email_sha256"] = hashlib.sha256(
                    ("wrong:" + doctor.clerk_id).encode()
                ).hexdigest()
            if name == "pilot_email":
                changes["email_snapshot"] = "other@synthetic.invalid"
            if name == "pilot_doctor":
                changes["doctor_id_snapshot"] = case(engine)["doctor_id"]
            if name == "pilot_future":
                changes["granted_at"] = at + timedelta(seconds=1)
            grant(
                session,
                doctor,
                practice,
                at,
                revoked=at if name in {"grant_revoked", "replacement"} else None,
                **changes,
            )
            if name == "replacement":
                grant(session, doctor, practice, at)
            if name == "duplicate":
                grant(session, doctor, practice, at, revoked=at + timedelta(days=1))
        if name.startswith("pilot") or name == "missing_member":
            session.delete(member)
        if name == "pilot_unonboarded":
            doctor.onboarding_completed_at = None
        if name == "disabled":
            doctor.deleted_at = at
        if name in {"inactive", "ending_before", "ending_at"}:
            member.seat_state = "inactive"
            if name.startswith("ending"):
                member.access_ends_at = at + timedelta(
                    microseconds=1 if name == "ending_before" else 0
                )
        if name == "revoked_member":
            member.access_revoked_at = at
            member.access_revoked_by_ref = "synthetic"
            member.access_revocation_reason = "synthetic"
            member.access_records_retain_until = at + timedelta(days=366 * 8)
        if name in {"baa_stale", "baa_future", "baa_effective"}:
            practice.baa_version = "synthetic-obsolete-version"
            if name != "baa_stale":
                practice.negotiated_baa_on_file = True
                practice.negotiated_baa_reference = "Synthetic negotiated agreement"
                practice.negotiated_baa_effective_date = at + timedelta(
                    seconds=1 if name == "baa_future" else 0
                )
        if name in {"no_entitlement", "exemption_before", "exemption_at"}:
            practice.billing_exempt = name != "no_entitlement"
            practice.billing_exempt_until = at + timedelta(
                microseconds=1 if name == "exemption_before" else 0
            )
        session.add_all([doctor, practice])
        session.commit()
    return args, at


def decide(engine, args, at, **kwargs):
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        return access.resolve_doctor_access(doctor, at=at, **kwargs)


@pytest.mark.parametrize(
    "name",
    [
        "normal",
        "missing_member",
        "pilot",
        "pilot_hash",
        "pilot_email",
        "pilot_doctor",
        "pilot_future",
        "pilot_unonboarded",
        "disabled",
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
def test_complete_decision_matches_previous_committed_state(
    engine, monkeypatch, name, require_entitlement
):
    args, at = scenario(engine, name)
    with old_queries(monkeypatch):
        old = decide(engine, args, at, require_entitlement=require_entitlement)
    new = decide(engine, args, at, require_entitlement=require_entitlement)
    assert new == old
    if name in {"disabled", "grant_revoked", "revoked_member", "inactive", "ending_at"}:
        assert not new.allowed


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
def test_entitlement_projection_counts_and_decisions_preserved(
    engine, monkeypatch, state, public
):
    args, at = scenario(engine, "normal")
    with session_for(engine) as session:
        practice = session.get(Practice, args["practice_id"])
        assert practice is not None
        practice.billing_exempt = False
        practice.subscription_status = state
        practice.trial_ends_at = at + timedelta(seconds=1)
        practice.acquisition_source = "public_self_service" if public else "managed"
        session.add(practice)
        session.commit()
    with old_queries(monkeypatch):
        old = decide(engine, args, at)
    assert decide(engine, args, at) == old


@pytest.mark.parametrize("invalid_doctor", [False, True])
def test_late_grant_preserves_cardinality_and_invalid_doctor_short_circuit(
    engine, monkeypatch, invalid_doctor
):
    args, at = scenario(engine, "duplicate")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        if invalid_doctor:
            assert doctor is not None
            doctor.email = "invalid-" + doctor.clerk_id
            session.add(doctor)
            session.commit()
        for function in [
            previous.active_pilot_access_grant,
            pilot.active_pilot_access_grant,
        ]:
            if invalid_doctor:
                assert function(doctor, at=at) is None
            else:
                with pytest.raises(MultipleResultsFound):
                    function(doctor, at=at)


def test_unlocked_no_pilot_keeps_original_read_boundaries(
    engine, monkeypatch, record_property
):
    args, at = scenario(engine, "normal")
    values = []
    for legacy in [True, False]:
        with session_for(engine) as session:
            doctor = session.get(Doctor, args["doctor_id"])
            with monkeypatch.context() as patch:
                if legacy:
                    patch.setattr(
                        access,
                        "active_pilot_access_grant",
                        previous.active_pilot_access_grant,
                    )
                    patch.setattr(
                        access,
                        "revoked_pilot_access_grant",
                        previous.revoked_pilot_access_grant,
                    )
                    patch.setattr(
                        access, "resolve_doctor_access", previous.resolve_doctor_access
                    )
                with query_counts(engine) as counts:
                    decision = access.resolve_doctor_access(doctor, at=at)
                values.append((decision, counts["all_selects"]))
    assert values[0][0] == values[1][0]
    assert [value[1] for value in values] == [6, 6]
    record_property("doctor_access_selects", {"before": 6, "after": 6})


@contextmanager
def mutate_before_read(engine, target, callback):
    fired = []
    running = False

    def before(_conn, _cursor, statement, _params, _context, _many):
        nonlocal running
        if running or fired or not target(" ".join(statement.lower().split())):
            return
        running = True
        try:
            callback()
        finally:
            running = False
        fired.append(True)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield fired
    finally:
        event.remove(engine, "before_cursor_execute", before)


@pytest.mark.parametrize("boundary", ["early_tombstone", "membership", "late_grant"])
@pytest.mark.parametrize("legacy", [False, True])
def test_unlocked_committed_mutation_at_retained_stage_has_same_denial(
    engine, monkeypatch, boundary, legacy
):
    args, at = scenario(engine, "pilot" if boundary == "late_grant" else "normal")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])

        def mutate():
            with Session(engine) as writer:
                if boundary == "membership":
                    practice = writer.get(Practice, args["practice_id"])
                    assert practice is not None
                    practice.baa_version = "synthetic-obsolete-version"
                    writer.add(practice)
                elif boundary == "late_grant":
                    assert doctor is not None
                    row = writer.exec(
                        select(PilotAccessGrandfatherGrant).where(
                            PilotAccessGrandfatherGrant.clerk_id == doctor.clerk_id
                        )
                    ).one()
                    revoke(row, at)
                    writer.add(row)
                else:
                    assert doctor is not None
                    owner = writer.get(Doctor, doctor.id)
                    practice = writer.get(Practice, args["practice_id"])
                    grant(writer, owner, practice, at, revoked=at)
                writer.commit()

        def target(sql):
            if boundary == "early_tombstone":
                return " from pilot_access_grandfather_grant " in sql
            if boundary == "membership":
                return " from practice_member " in sql
            return " from doctor " in sql

        with monkeypatch.context() as patch:
            if legacy:
                patch.setattr(
                    access,
                    "active_pilot_access_grant",
                    previous.active_pilot_access_grant,
                )
                patch.setattr(
                    access,
                    "revoked_pilot_access_grant",
                    previous.revoked_pilot_access_grant,
                )
                patch.setattr(
                    access, "resolve_doctor_access", previous.resolve_doctor_access
                )
            with mutate_before_read(engine, target, mutate) as fired:
                result = access.resolve_doctor_access(doctor, at=at)
            assert fired == [True] and not result.allowed


def test_identity_map_refresh_behavior_remains_explicit(engine):
    args, at = scenario(engine, "pilot")
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        # Existing helper respects its caller's identity map. Both query shapes
        # must have identical behavior, including explicit subsequent refresh.
        with Session(engine) as writer:
            assert doctor is not None
            current = writer.get(Doctor, doctor.id)
            assert current is not None
            current.email = f"changed-{doctor.id}@synthetic.invalid"
            writer.add(current)
            writer.commit()
        assert previous.active_pilot_access_grant(doctor, at=at) is not None
        assert pilot.active_pilot_access_grant(doctor, at=at) is not None
        session.expire_all()
        assert previous.active_pilot_access_grant(doctor, at=at) is None
        assert pilot.active_pilot_access_grant(doctor, at=at) is None


@pytest.mark.parametrize("boundary", ["early_tombstone", "membership", "late_grant"])
def test_unlocked_interquery_mutation_cannot_turn_old_denial_into_allow(
    engine, monkeypatch, boundary, record_property
):
    results = []
    for legacy in [True, False]:
        args, at = scenario(engine, "pilot" if boundary == "late_grant" else "normal")
        with session_for(engine) as session:
            doctor = session.get(Doctor, args["doctor_id"])
            fired = []
            running = False

            def after(_conn, _cursor, statement, _params, _context, _many):
                nonlocal running
                if fired or running:
                    return
                sql = " ".join(statement.lower().split())
                target = {
                    "early_tombstone": "pilot_access_grandfather_grant",
                    "membership": "practice_member",
                    "late_grant": "doctor",
                }[boundary]
                if " from " + target + " " not in sql:
                    return
                running = True
                try:
                    with Session(engine) as writer:
                        practice = writer.get(Practice, args["practice_id"])
                        if boundary == "membership":
                            assert practice is not None
                            practice.baa_version = "synthetic-obsolete-version"
                            writer.add(practice)
                        elif boundary == "late_grant":
                            assert doctor is not None
                            row = writer.exec(
                                select(PilotAccessGrandfatherGrant).where(
                                    PilotAccessGrandfatherGrant.clerk_id
                                    == doctor.clerk_id
                                )
                            ).one()
                            # A revocation begun at this decision's time commits
                            # now; its timestamp is valid and <= the supplied at.
                            revoke(row, at)
                            writer.add(row)
                        else:
                            assert doctor is not None
                            owner = writer.get(Doctor, doctor.id)
                            grant(writer, owner, practice, at, revoked=at)
                        writer.commit()
                    fired.append(True)
                finally:
                    running = False

            with monkeypatch.context() as patch:
                if legacy:
                    patch.setattr(
                        access,
                        "active_pilot_access_grant",
                        previous.active_pilot_access_grant,
                    )
                    patch.setattr(
                        access,
                        "revoked_pilot_access_grant",
                        previous.revoked_pilot_access_grant,
                    )
                    patch.setattr(
                        access, "resolve_doctor_access", previous.resolve_doctor_access
                    )
                event.listen(engine, "after_cursor_execute", after)
                try:
                    result = access.resolve_doctor_access(doctor, at=at)
                finally:
                    event.remove(engine, "after_cursor_execute", after)
            assert fired == [True]
            results.append(result)
    record_property(
        "unlocked_race",
        {
            "stage": boundary,
            "old_allowed": results[0].allowed,
            "new_allowed": results[1].allowed,
        },
    )
    assert not (not results[0].allowed and results[1].allowed)
