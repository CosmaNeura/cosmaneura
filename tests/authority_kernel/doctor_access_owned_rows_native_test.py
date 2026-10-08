"""Real native router/SQL with invented identities and downstream Scribe."""

import inspect
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services import (
    companion_appointment_setup,
    companion_encounters,
    scribe_encounters,
)
from app.services import doctor_access as access
from app.services import pilot_access_grandfather as pilot
from app.services import provider_record_authority as records
from app.services.doctor_access_row_ownership import DoctorAccessRowOwnership
from app.services.practice_access import set_billing_exemption
from app.services.practice_member_access import revoke_member_access

from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, col, select, text

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.doctor_access_query_equivalence_test import grant
from tests.authority_kernel.provider_record_lineage_reads_test import original_rows
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

engine, native, workflow = _engine, _native, _workflow


def test_existing_companion_connection_setup_and_revocation_on_owned_database(
    engine, monkeypatch
):
    # Reuse the existing real service acceptance body with this suite's owned
    # engine; its original fixture targets a different dedicated local DB.
    from tests.authority_kernel.companion_onboarding_flow_test import (
        test_reviewed_connection_to_current_capture_and_revocation,
    )

    test_reviewed_connection_to_current_capture_and_revocation(engine, monkeypatch)


@pytest.mark.parametrize(
    "kind,before,after,eligible,total",
    [
        ("mount", 1043, 963, 20, 45),
        ("creator_review", 821, 757, 16, 36),
        ("capabilities", 1031, 967, 16, 44),
    ],
)
def test_real_native_reduces_only_explicit_owned_provider_queries(
    native, engine, monkeypatch, kind, before, after, eligible, total, record_property
):
    # Preserve this older comparison's original record-lineage baseline.
    monkeypatch.setattr(records, "_record_lineage_rows", original_rows)
    # Isolate the older owned-access-query change in this original regression.
    # The joined final-provider path has its own response/lock/count comparison.
    monkeypatch.setattr(
        companion_appointment_setup,
        "_locked_provider",
        lambda session, *, expected, doctor_id, args: companion_encounters._provider(
            session, **args
        ),
    )

    # Preserve this older query/call-site comparison's original full final
    # lookup routine. The new lookup validator has separate real-path evidence.
    def original_lookup_final(session, *, expected, doctor_id, args):
        connection, current_doctor, _ = companion_appointment_setup._current_provider(
            session, args
        )
        return connection, current_doctor

    monkeypatch.setattr(scribe_encounters, "_locked_provider", original_lookup_final)
    original = access.resolve_doctor_access
    original_matches = DoctorAccessRowOwnership.matches
    observed = Counter()
    locks = []

    def resolve(doctor, **kwargs):
        proof = kwargs.get("_ownership")
        if proof is not None:
            assert original_matches(
                proof, proof._session, doctor, practice_id=kwargs["_practice_id"]
            )
            caller_frame = inspect.currentframe()
            assert caller_frame is not None
            try:
                assert caller_frame.f_back is not None
                assert caller_frame.f_back.f_back is not None
                assert caller_frame.f_back.f_back.f_code.co_name == "_provider"
            finally:
                # Retaining our own frame creates a cycle and keeps ORM rows
                # alive, changing the SQLAlchemy identity-map query counts.
                del caller_frame
        observed[proof is not None] += 1
        return original(doctor, **kwargs)

    def capture_locks(_connection, _cursor, statement, *_args):
        # Shapes only, never parameters or rows.
        normalized = " ".join(statement.lower().split())
        if " for update" in normalized:
            locks.append(normalized)

    monkeypatch.setattr(access, "resolve_doctor_access", resolve)
    runs = []
    for optimized in (False, True):
        observed.clear()
        locks.clear()
        with monkeypatch.context() as patch:
            if not optimized:
                patch.setattr(
                    DoctorAccessRowOwnership, "matches", lambda *_args, **_kwargs: False
                )
            event.listen(engine, "before_cursor_execute", capture_locks)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", capture_locks)
        assert response.status_code == 200
        assert observed == {True: eligible, False: total - eligible}
        runs.append((counts["all_selects"], list(locks)))
    assert [x[0] for x in runs] == [before, after]
    assert runs[0][1] == runs[1][1]  # same lock statements and ordering
    record_property("selects", f"{before}->{after}")
    record_property("access_decisions", total)
    record_property("explicit_owned_decisions", eligible)


@pytest.mark.parametrize("writer_kind", ["billing", "membership", "pilot_revoke"])
def test_actual_writer_waits_then_commits_and_next_native_open_denies(
    native, engine, monkeypatch, writer_kind, record_property
):
    at = datetime.now(UTC)
    with session_for(engine) as setup:
        doctor = setup.get(Doctor, native.args["doctor_id"])
        practice = lock_practice_seats(setup, native.args["practice_id"])
        assert doctor is not None
        member = setup.exec(
            select(PracticeMember).where(col(PracticeMember.doctor_id) == doctor.id)
        ).one()
        member_id = member.id
        owner_id = member.id
        if writer_kind == "membership":
            # A distinct invented owner invokes the real workforce-revoke path.
            suffix = uuid4().hex
            practice_owner = Doctor(
                clerk_id="user_lock_" + suffix,
                email=suffix + "@synthetic.invalid",
                biography=None,
                last_active_at=at,
            )
            setup.add(practice_owner)
            setup.flush()
            assert practice is not None
            owner = PracticeMember(
                practice_id=practice.id,
                doctor_id=practice_owner.id,
                role="owner",
                attested_at=at,
            )
            member.role = "workforce"
            setup.add_all([owner, member])
            setup.flush()
            owner_id = owner.id
        if writer_kind == "pilot_revoke":
            grant(setup, doctor, practice, at)
        setup.commit()

    def mutate(*, bounded):
        with session_for(engine) as writer:
            if bounded:
                writer.execute(text("SET LOCAL lock_timeout = '150ms'"))
            if writer_kind == "billing":
                practice = writer.get(Practice, native.args["practice_id"])
                assert practice is not None
                return set_billing_exemption(
                    practice,
                    operator_ref="synthetic-owned-proof",
                    reason="Synthetic lock proof",
                    expires_at=None,
                    clear=True,
                )
            if writer_kind == "membership":
                owner = writer.get(PracticeMember, owner_id)
                assert owner is not None
                # The real scheduling policy remains; use an invented earlier
                # clock so the scheduled cutoff has passed at the next request.
                return revoke_member_access(
                    writer,
                    owner_member=owner,
                    member_id=member_id,
                    clock=lambda: at - timedelta(days=2),
                )
            subjects = [native.args["verified_clerk_subject"]]
            options = dict(
                operator_ref="synthetic-owned-proof", reason="Synthetic lock proof"
            )
            return pilot.revoke_pilot_access_grants(
                subjects,
                confirm_plan_sha256=pilot.pilot_access_revoke_plan_sha256(
                    subjects, **options
                ),
                now=at,
                **options,
            )

    original = access.resolve_doctor_access
    observed = []

    def resolve(doctor, **kwargs):
        if not observed and kwargs.get("_ownership") is not None:
            with pytest.raises(DBAPIError) as error:
                mutate(bounded=True)
            assert error.value.orig is not None
            assert getattr(error.value.orig, "sqlstate", None) == "55P03"
            # Independently inspect side effects after the writer rolled back.
            with Session(engine) as check:
                stored_practice_2 = check.get(Practice, native.args["practice_id"])
                assert stored_practice_2 is not None
                assert stored_practice_2.billing_exempt
                stored_doctor = check.get(Doctor, native.args["doctor_id"])
                assert stored_doctor is not None
                assert stored_doctor.deleted_at is None
                unchanged = check.get(PracticeMember, member_id)
                assert unchanged is not None
                assert (
                    unchanged.access_revoked_at is None
                    and unchanged.access_ends_at is None
                )
                if writer_kind == "pilot_revoke":
                    assert (
                        check.exec(
                            select(PilotAccessGrandfatherGrant).where(
                                col(PilotAccessGrandfatherGrant.clerk_id)
                                == native.args["verified_clerk_subject"]
                            )
                        )
                        .one()
                        .revoked_at
                        is None
                    )
            observed.append("writer_blocked_no_side_effect")
            with query_counts(engine) as counts:
                result = original(doctor, **kwargs)
            assert counts["all_selects"] == 3
            return result
        return original(doctor, **kwargs)

    monkeypatch.setattr(access, "resolve_doctor_access", resolve)
    assert native.post().status_code == 200
    assert observed == ["writer_blocked_no_side_effect"]
    mutate(bounded=False)
    with Session(engine) as check:
        if writer_kind == "billing":
            stored_practice = check.get(Practice, native.args["practice_id"])
            assert stored_practice is not None
            assert not stored_practice.billing_exempt
        elif writer_kind == "membership":
            stored_practice_member = check.get(PracticeMember, member_id)
            assert stored_practice_member is not None
            assert stored_practice_member.access_ends_at is not None
            assert stored_practice_member.access_ends_at < at
        else:
            stored_doctor_2 = check.get(Doctor, native.args["doctor_id"])
            assert stored_doctor_2 is not None
            assert stored_doctor_2.deleted_at == at
            assert (
                check.exec(
                    select(PilotAccessGrandfatherGrant).where(
                        col(PilotAccessGrandfatherGrant.clerk_id)
                        == native.args["verified_clerk_subject"]
                    )
                )
                .one()
                .revoked_at
                == at
            )
    assert native.post().status_code == 403
    record_property("writer", writer_kind)
    record_property(
        "result", "blocked before mutation; committed afterward; fresh opening denied"
    )
