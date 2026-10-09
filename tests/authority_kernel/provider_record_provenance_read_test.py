"""Exact provenance parity on invocation-owned fictional PostgreSQL."""

from uuid import uuid4

import pytest

from app.services import provider_record_authority as records

from app.models.authority import AuthorityPerson, AuthorityPersonOrigin
from sqlalchemy import event, select
from sqlalchemy.exc import MultipleResultsFound

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.provider_record_lineage_reads_test import (
    binding_and_scope,
    validate,
)
from tests.authority_kernel.provider_record_lineage_reads_test import engine as _engine
from tests.authority_kernel.provider_record_lineage_reads_test import native as _native
from tests.authority_kernel.provider_record_lineage_reads_test import (
    workflow as _workflow,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import query_counts

engine, native, workflow = _engine, _native, _workflow


def previous_rows(session, scope, binding):
    return session.exec(
        select(
            records.ProviderPracticeAuthority,
            records.Appointment,
            records.Patient,
            AuthorityPerson,
        )
        .select_from(records.ProviderPracticeAuthority)
        .join(records.Appointment, records.Appointment.id == scope.appointment_id)
        .join(records.Patient, records.Patient.id == scope.patient_id)
        .join(AuthorityPerson, AuthorityPerson.id == binding.person_id)
        .where(records.ProviderPracticeAuthority.id == scope.practice_authority_id)
        .execution_options(populate_existing=True)
    ).one_or_none()


@pytest.mark.parametrize("kind", ["mount", "creator_review", "capabilities"])
def test_full_native_receipt_and_lock_parity_with_fewer_origin_queries(
    native, engine, monkeypatch, kind, record_property
):
    runs = []
    for combined in (False, True):
        locks = []

        def observe(_connection, _cursor, statement, *_args):
            shape = " ".join(statement.lower().split())
            if " for update" in shape:
                locks.append(shape)

        with monkeypatch.context() as patch:
            if not combined:
                patch.setattr(records, "_record_lineage_rows", previous_rows)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                with query_counts(engine) as count:
                    response = native.post(kind)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        assert response.status_code == 200
        value = response.json()["result"]
        published = value["encounter"] if kind == "mount" else value["receipt"]
        runs.append((published, count["all_selects"], locks))
    assert runs[0][0] == runs[1][0]
    assert runs[0][2] == runs[1][2]
    assert runs[1][1] < runs[0][1]
    record_property("previous_selects", runs[0][1])
    record_property("combined_selects", runs[1][1])


@pytest.mark.parametrize("change", ["person", "source", "kind", "duplicate"])
@pytest.mark.parametrize("combined", [False, True])
def test_exact_origin_filters_and_duplicate_refusal(
    native, engine, monkeypatch, change, combined
):
    if not combined:
        monkeypatch.setattr(records, "_record_lineage_rows", previous_rows)
    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        if change != "duplicate":
            person = AuthorityPerson(lifecycle_state="patient", status="active")
            session.add(person)
            session.flush()
            binding.person_id = person.id
            session.add(binding)
            session.flush()
        origin_person_id = binding.person_id
        if change == "person":
            other = AuthorityPerson(lifecycle_state="patient", status="active")
            session.add(other)
            session.flush()
            origin_person_id = other.id
        # Retained provenance is append-only: create mismatched fictional rows
        # rather than modifying or removing the fixture's original origin.
        session.add(
            AuthorityPersonOrigin(
                person_id=origin_person_id,
                origin="self_service" if change == "kind" else "provider_entry",
                source_ref="invented_wrong_" + uuid4().hex
                if change == "source"
                else str(scope.patient_id),
            )
        )
        session.flush()
        with pytest.raises(
            MultipleResultsFound
            if change == "duplicate"
            else records.ProviderRecordScopeDenied
        ):
            validate(session, binding, native)


@pytest.mark.parametrize("combined", [False, True])
def test_lineage_denial_precedes_duplicate_provenance(
    native, engine, monkeypatch, combined
):
    if not combined:
        monkeypatch.setattr(records, "_record_lineage_rows", previous_rows)
    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        person = session.get(AuthorityPerson, binding.person_id)
        assert person is not None
        person.status = "revoked"
        session.add(person)
        session.add(
            AuthorityPersonOrigin(
                person_id=binding.person_id,
                origin="provider_entry",
                source_ref=str(scope.patient_id),
            )
        )
        session.flush()
        with pytest.raises(records.ProviderRecordScopeDenied):
            validate(session, binding, native)


def test_origin_reads_are_bounded_and_do_not_use_identity_map_as_permission(
    native, engine
):
    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        origin = session.exec(
            records.select(AuthorityPersonOrigin).where(
                AuthorityPersonOrigin.person_id == binding.person_id,
                AuthorityPersonOrigin.origin == "provider_entry",
                AuthorityPersonOrigin.source_ref == str(scope.patient_id),
            )
        ).one()
        origin.origin = "self_service"
        with session.no_autoflush:
            accepted = validate(session, binding, native)
            assert accepted is not None and accepted.id == scope.id
            assert origin.origin == "provider_entry"
        session.add_all(
            AuthorityPersonOrigin(
                person_id=binding.person_id,
                origin="provider_entry",
                source_ref=str(scope.patient_id),
            )
            for _ in range(3)
        )
        session.flush()
        current = records._record_lineage_rows(session, scope, binding)
        assert isinstance(current, records._RecordLineageRows)
        assert len(current.origins) == 2
        with pytest.raises(MultipleResultsFound):
            validate(session, binding, native)


def test_session_adapter_preserves_original_read_and_exact_origin_denial(
    native, engine, monkeypatch
):
    original = records._record_lineage_rows

    class Adapter:
        def __init__(self, session):
            self.session = session

        def exec(self, statement):
            return self.session.exec(statement)

    def adapter_rows(session, scope, binding):
        return original(Adapter(session), scope, binding)

    with session_for(engine) as session:
        binding, scope = binding_and_scope(session, native)
        with query_counts(engine) as combined:
            accepted = validate(session, binding, native)
        assert accepted is not None and accepted.id == scope.id
        monkeypatch.setattr(records, "_record_lineage_rows", adapter_rows)
        with query_counts(engine) as separate:
            accepted = validate(session, binding, native)
        assert accepted is not None and accepted.id == scope.id
        assert separate["all_selects"] == combined["all_selects"] + 1
        session.add(
            AuthorityPersonOrigin(
                person_id=binding.person_id,
                origin="provider_entry",
                source_ref=str(scope.patient_id),
            )
        )
        session.flush()
        with pytest.raises(MultipleResultsFound):
            validate(session, binding, native)
