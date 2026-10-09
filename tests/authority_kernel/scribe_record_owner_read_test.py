"""Current exact-bound record owner reads under existing synthetic provider locks."""

from datetime import UTC, datetime

import pytest
from typeid import TypeID

from app.services import doctor_access_row_ownership as owned
from app.services import provider_record_authority as records

from app.models.authority import AuthorityPerson, AuthorityPersonOrigin
from app.models.doctor import Doctor
from sqlalchemy import event
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm.attributes import set_committed_value

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.provider_record_lineage_reads_test import (
    binding_and_scope,
    validate,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_native_record_owner_receipts_and_exact_locks_match_separate_reads(
    native, engine, monkeypatch, kind, record_property
):
    runs = []
    for previous in (True, False):
        locks = []

        def observe(_connection, _cursor, statement, *_):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if previous:
                patch.setattr(records, "capture_owns_subject_rows", lambda *_: False)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        result = response.json()["result"]
        runs.append(
            (
                result["encounter"] if kind == "mount" else result["receipt"],
                counts["all_selects"],
                locks,
            )
        )
        assert engine.pool.checkedout() == 0 and owned._CAPTURE_ROWS.get() is None
    assert runs[0][0] == runs[1][0] and runs[0][2] == runs[1][2]
    assert runs[1][1] < runs[0][1]
    record_property("operation", kind)
    record_property("previous_selects", runs[0][1])
    record_property("current_selects", runs[1][1])
    record_property("exact_lock_sequence_preserved", True)


def owned_record(session, native):
    return owned.capture_doctor_access_rows(
        session,
        practice_id=native.args["practice_id"],
        doctor_id=native.args["doctor_id"],
        subject=native.args["verified_clerk_subject"],
    )


@pytest.mark.parametrize(
    "invalid", ["subject", "expiration", "commit", "rollback", "savepoint"]
)
def test_invalid_record_owner_evidence_uses_original_reads(
    native, engine, monkeypatch, invalid
):
    with session_for(engine) as session:
        binding, _ = binding_and_scope(session, native)
        with owned_record(session, native) as locked:
            assert locked is not None
            if invalid == "subject":
                set_committed_value(locked[1], "clerk_id", "user_changed_synthetic")
            elif invalid == "expiration":
                session.expire_all()
            elif invalid == "commit":
                session.commit()
            elif invalid == "rollback":
                session.rollback()
            else:
                with session.begin_nested():
                    pass

            def forbidden(*_):
                pytest.fail("Invalid evidence must use original record owner reads")

            monkeypatch.setattr(records, "_record_owner_rows", forbidden)
            try:
                current = validate(session, binding, native)
            except records.ProviderRecordScopeDenied:
                assert invalid == "subject"
            else:
                assert current is not None


@pytest.mark.parametrize("missing", [False, True])
def test_exact_scope_absence_and_doctor_refresh_match_original(
    native, engine, monkeypatch, missing
):
    for previous in (True, False):
        with session_for(engine) as session, monkeypatch.context() as patch:
            binding, scope = binding_and_scope(session, native)
            with owned_record(session, native) as locked:
                assert locked is not None
                if previous:
                    patch.setattr(
                        records, "capture_owns_subject_rows", lambda *_: False
                    )
                if missing:
                    binding.id = TypeID("person_binding")
                    with session.no_autoflush:
                        assert validate(session, binding, native) is None
                else:
                    doctor = locked[1]
                    set_committed_value(doctor, "email", "stale@synthetic.invalid")
                    with session.no_autoflush:
                        assert validate(session, binding, native).id == scope.id
                    assert doctor.email != "stale@synthetic.invalid"


@pytest.mark.parametrize("change", ["disabled", "subject"])
def test_committed_doctor_change_after_existing_locks_is_denied(native, engine, change):
    assert native.post().status_code == 200
    with session_for(engine) as writer:
        doctor = writer.get(Doctor, native.args["doctor_id"])
        if change == "disabled":
            doctor.deleted_at = datetime.now(UTC)
        else:
            doctor.clerk_id = "user_changed_record_owner"
        writer.add(doctor)
        writer.commit()
    assert native.post().status_code == 403


@pytest.mark.parametrize("origin_count", [0, 2])
def test_owned_join_keeps_separate_exact_origin_refusal(native, engine, origin_count):
    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        with owned_record(session, native) as locked:
            assert locked is not None
            if origin_count == 0:
                person = AuthorityPerson(lifecycle_state="patient", status="active")
                session.add(person)
                session.flush()
                binding.person_id = person.id
                session.add(binding)
                session.flush()
            else:
                session.add(
                    AuthorityPersonOrigin(
                        person_id=binding.person_id,
                        origin="provider_entry",
                        source_ref=str(scope.patient_id),
                    )
                )
                session.flush()
            with pytest.raises(
                records.ProviderRecordScopeDenied
                if origin_count == 0
                else MultipleResultsFound
            ):
                validate(session, binding, native)


def test_joined_scope_and_doctor_reads_do_not_skip_record_lineage(
    native, engine, monkeypatch
):
    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        with owned_record(session, native) as locked:
            assert locked is not None
            original = records._record_lineage_rows
            calls = []

            def lineage(*args):
                calls.append(True)
                return original(*args)

            monkeypatch.setattr(records, "_record_lineage_rows", lineage)
            assert validate(session, binding, native).id == scope.id
            assert calls == [True]
            set_committed_value(scope, "patient_id", TypeID("patient"))
            # The new query refreshes the stored exact scope as well as Doctor;
            # persisted scope remains current and the later lineage still runs.
            with session.no_autoflush:
                assert validate(session, binding, native).id == scope.id
            assert calls == [True, True]
