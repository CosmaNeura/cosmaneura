"""One fresh joined access-input read under actual locks and explicit clocks."""

from dataclasses import replace

import pytest

from app.services import doctor_access as access
from app.services import pilot_access_grandfather as pilot
from app.services.doctor_access_row_ownership import lock_doctor_access_rows

from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm.attributes import set_committed_value

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.doctor_access_owned_rows_test import owned_case
from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts

engine = _engine


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
def test_explicit_time_decisions_match_current_individual_queries(
    engine, monkeypatch, name, require_entitlement
):
    args, at = owned_case(engine, name)
    with (
        session_for(engine) as session,
        lock_doctor_access_rows(
            session,
            practice_id=args["practice_id"],
            doctor_id=args["doctor_id"],
            subject=args["verified_clerk_subject"],
        ) as locked,
    ):
        assert locked is not None
        doctor, proof = locked[1], locked[3]
        options = dict(
            at=at,
            require_entitlement=require_entitlement,
            _ownership=proof,
            _practice_id=args["practice_id"],
        )
        with monkeypatch.context() as patch:
            patch.setattr(access, "owned_provider_access_rows", lambda *_a, **_kw: None)
            expected = access.resolve_doctor_access(doctor, **options)
        assert access.resolve_doctor_access(doctor, **options) == expected


def test_join_removes_one_fresh_read_only_for_explicit_time(engine, monkeypatch):
    args, at = owned_case(engine)
    with (
        session_for(engine) as session,
        lock_doctor_access_rows(
            session,
            practice_id=args["practice_id"],
            doctor_id=args["doctor_id"],
            subject=args["verified_clerk_subject"],
        ) as locked,
    ):
        assert locked is not None
        doctor, proof = locked[1], locked[3]
        options = dict(at=at, _ownership=proof, _practice_id=args["practice_id"])
        with monkeypatch.context() as patch:
            patch.setattr(access, "owned_provider_access_rows", lambda *_a, **_kw: None)
            with query_counts(engine) as before:
                expected = access.resolve_doctor_access(doctor, **options)
        with query_counts(engine) as after:
            assert access.resolve_doctor_access(doctor, **options) == expected
        assert before["all_selects"] == 3 and after["all_selects"] == 2
        with query_counts(engine) as default_clock:
            assert access.resolve_doctor_access(
                doctor, _ownership=proof, _practice_id=args["practice_id"]
            ).allowed
        assert default_clock["all_selects"] == 3


@pytest.mark.parametrize("invalid", [False, True])
def test_join_validates_doctor_before_duplicate_grant_refusal(engine, invalid):
    args, at = owned_case(engine, "duplicate")
    with (
        session_for(engine) as session,
        lock_doctor_access_rows(
            session,
            practice_id=args["practice_id"],
            doctor_id=args["doctor_id"],
            subject=args["verified_clerk_subject"],
        ) as locked,
    ):
        assert locked is not None
        doctor, proof = locked[1], locked[3]
        if invalid:
            set_committed_value(doctor, "email", "invalid")
            rows = pilot.owned_provider_access_rows(doctor, at=at, ownership=proof)
            assert rows is not None and rows.pilot_grant is None
        else:
            with pytest.raises(MultipleResultsFound):
                pilot.owned_provider_access_rows(doctor, at=at, ownership=proof)


@pytest.mark.parametrize("change", ["email", "subject", "proof", "expiration"])
def test_join_loaded_identity_and_proof_fallback(engine, change, monkeypatch):
    args, at = owned_case(engine, "pilot")
    with (
        session_for(engine) as session,
        lock_doctor_access_rows(
            session,
            practice_id=args["practice_id"],
            doctor_id=args["doctor_id"],
            subject=args["verified_clerk_subject"],
        ) as locked,
    ):
        assert locked is not None
        doctor, proof = locked[1], locked[3]
        if change == "email":
            set_committed_value(doctor, "email", "changed@synthetic.invalid")
        elif change == "subject":
            set_committed_value(doctor, "clerk_id", "user_changed_synthetic")
        elif change == "proof":
            proof = replace(proof, _issuer=object())
        else:
            session.expire_all()
        options = dict(at=at, _ownership=proof, _practice_id=args["practice_id"])
        with monkeypatch.context() as patch:
            patch.setattr(access, "owned_provider_access_rows", lambda *_a, **_kw: None)
            expected = access.resolve_doctor_access(doctor, **options)
        assert access.resolve_doctor_access(doctor, **options) == expected
