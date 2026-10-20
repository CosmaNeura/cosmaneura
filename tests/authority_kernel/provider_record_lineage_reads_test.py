"""Current lineage validation on invocation-owned synthetic PostgreSQL."""

from typing import cast

import pytest

from app.services import provider_record_authority as records

from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from sqlalchemy import event
from sqlalchemy.exc import MultipleResultsFound
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import session_for
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


def original_rows(session, scope, binding):
    # Exact four reads from 4c0b69df, at the same post-access boundary.
    return (
        session.get(ProviderPracticeAuthority, scope.practice_authority_id),
        session.get(Appointment, scope.appointment_id, populate_existing=True),
        session.get(Patient, scope.patient_id, populate_existing=True),
        session.get(AuthorityPerson, binding.person_id, populate_existing=True),
    )


def binding_and_scope(session, native):
    binding = session.exec(
        select(AuthorityPersonBinding).where(
            AuthorityPersonBinding.binding_ref == native.row["appointment_ref"]
        )
    ).one()
    scope = session.exec(
        select(ProviderRecordAuthority).where(
            ProviderRecordAuthority.binding_id == binding.id
        )
    ).one()
    return binding, scope


def validate(session, binding, native):
    return records.validate_provider_record_scope(
        session=session,
        binding=binding,
        actor_id=binding.actor_id,
        clerk_subject=native.args["verified_clerk_subject"],
    )


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_native_preserves_receipts_and_locks_with_fewer_lineage_reads(
    native, engine, monkeypatch, kind, record_property
):
    results = []
    for joined in (False, True):
        locks = []

        def observe(_connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if not joined:
                patch.setattr(records, "_record_lineage_rows", original_rows)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as counts:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        result = response.json()["result"]
        published = result["encounter"] if kind == "mount" else result["receipt"]
        results.append((published, counts["all_selects"], locks))
    assert results[0][0] == results[1][0]
    assert results[0][2] == results[1][2]
    assert results[1][1] < results[0][1]
    record_property("operation", kind)
    record_property("previous_selects", results[0][1])
    record_property("joined_selects", results[1][1])


@pytest.mark.parametrize("change", ["appointment", "patient", "person", "mapping"])
def test_changed_lineage_rows_are_rejected_at_original_boundary(
    native, engine, monkeypatch, change
):
    for joined in (False, True):
        with session_for(engine) as session, monkeypatch.context() as patch:
            binding, scope = binding_and_scope(session, native)
            from uuid import uuid4

            other_doctor = Doctor(
                clerk_id="user_lineage_" + uuid4().hex,
                email=uuid4().hex + "@synthetic.invalid",
                biography=None,
            )
            other_tenant = AuthorityTenant(status="active")
            session.add_all([other_doctor, other_tenant])
            session.flush()
            other_patient = Patient(
                doctor_id=other_doctor.id, name="Invented other lineage patient"
            )
            session.add(other_patient)
            session.flush()
            original_practice = records._current_practice

            def changed(current_session, doctor):
                practice = original_practice(current_session, doctor)
                if change == "person":
                    row = session.get(AuthorityPerson, binding.person_id)
                    assert row is not None
                    row.status = "revoked"
                elif change == "appointment":
                    row = session.get(Appointment, scope.appointment_id)
                    assert row is not None
                    row.patient_id = other_patient.id
                elif change == "patient":
                    row = session.get(Patient, scope.patient_id)
                    assert row is not None
                    row.doctor_id = other_doctor.id
                else:
                    row = session.get(
                        ProviderPracticeAuthority, scope.practice_authority_id
                    )
                    assert row is not None
                    row.tenant_id = other_tenant.id
                session.add(row)
                session.flush()
                return practice

            patch.setattr(records, "_current_practice", changed)
            if not joined:
                patch.setattr(records, "_record_lineage_rows", original_rows)
            with pytest.raises(records.ProviderRecordScopeDenied):
                validate(session, binding, native)


@pytest.mark.parametrize("origin_count", [0, 2])
def test_separate_exact_provenance_check_still_denies_missing_or_duplicate(
    native, engine, origin_count
):
    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        if origin_count == 0:
            # A new fictional person has no provenance; preserve every retained
            # origin and its append-only guards even in this isolated fixture.
            person = AuthorityPerson(lifecycle_state="patient", status="active")
            session.add(person)
            session.flush()
            binding.person_id = person.id
        else:
            from app.models.authority import AuthorityPersonOrigin

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


@pytest.mark.parametrize(
    "model", [Appointment, Patient, AuthorityPerson, ProviderPracticeAuthority]
)
def test_join_refreshes_stale_orm_without_using_it_as_permission(native, engine, model):
    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        identifier = {
            Appointment: scope.appointment_id,
            Patient: scope.patient_id,
            AuthorityPerson: binding.person_id,
            ProviderPracticeAuthority: scope.practice_authority_id,
        }[model]
        row = session.get(model, identifier)
        assert row is not None
        field = (
            "status"
            if model is AuthorityPerson
            else "tenant_id"
            if model is ProviderPracticeAuthority
            else "doctor_id"
        )
        previous = getattr(row, field)
        # Only an unsaved stale identity-map value; the DB lineage remains
        # current. The joined boundary explicitly reads the DB in every case.
        setattr(row, field, "revoked" if model is AuthorityPerson else None)
        with session.no_autoflush:
            current = records._record_lineage_rows(session, scope, binding)
        assert current is not None and getattr(row, field) == previous


@pytest.mark.parametrize(
    "missing", ["scope", "mapping", "appointment", "patient", "person"]
)
def test_missing_exact_lineage_cannot_fall_back_to_another_record(
    native, engine, missing
):
    from typeid import TypeID

    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        # Unsaved fixture-only keys under no-autoflush model an absent exact
        # row, without deleting retained fictional records or changing guards.
        if missing == "scope":
            binding.id = cast(TypeIDType, TypeID("person_binding"))
            with session.no_autoflush:
                assert validate(session, binding, native) is None
            return
        if missing == "person":
            binding.person_id = cast(TypeIDType, TypeID("authority_person"))
        else:
            field, prefix = {
                "mapping": ("practice_authority_id", "provider_practice_authority"),
                "appointment": ("appointment_id", "appointment"),
                "patient": ("patient_id", "patient"),
            }[missing]
            setattr(scope, field, TypeID(prefix))
        with session.no_autoflush:
            assert records._record_lineage_rows(session, scope, binding) is None
