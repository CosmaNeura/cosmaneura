"""Missing-DOB updates use exact visit authority and retained metadata-only receipts."""

from datetime import datetime
from uuid import uuid4

import pytest
from companion_provider_provisioning_test import count, session_for
from typeid import TypeID

from app.services import authority, scribe_encounters

from app.models.appointment import Appointment
from app.models.authority import AuthorityAuditEvent
from app.models.patient import Patient
from app.models.scribe_encounter_creation import ScribeEncounterCreation
from sqlmodel import select

from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_encounters_test import workflow as _workflow

engine = _engine
workflow = _workflow


def missing_visit(workflow):
    _, _, request, invoke = workflow
    visit = invoke(
        request(
            patient={
                "kind": "new",
                "name": "Invented missing birthday",
                "date_of_birth": None,
            }
        )
    )["encounter"]
    update = request(
        "add_birth_date",
        appointment_ref=visit["appointment_ref"],
        patient_ref=visit["patient_ref"],
        change_key=str(uuid4()),
        date_of_birth="1980-02-03",
    )
    return visit, update, invoke


def audit_receipts(engine, key):
    with session_for(engine) as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.event_type == "scribe.patient.birth_date_added",
                AuthorityAuditEvent.resource_ref == "scribe_birth_date:" + str(key),
            )
        ).all()


def test_missing_dob_fills_once_and_retry_preserves_exact_visit(engine, workflow):
    visit, update, invoke = missing_visit(workflow)
    before = [
        count(engine, model)
        for model in (Patient, Appointment, ScribeEncounterCreation)
    ]
    first = invoke(update)
    second = invoke(update.model_copy(update={"request_nonce": uuid4()}))
    assert first["encounter"] == second["encounter"]
    assert first["encounter"]["appointment_ref"] == visit["appointment_ref"]
    assert first["encounter"]["patient_ref"] == visit["patient_ref"]
    assert first["encounter"]["date_of_birth"] == "1980-02-03"
    assert first["change_key"] == str(update.change_key)
    assert [
        count(engine, model)
        for model in (Patient, Appointment, ScribeEncounterCreation)
    ] == before
    receipts = audit_receipts(engine, update.change_key)
    assert len(receipts) == 1
    assert (
        "1980" not in receipts[0].model_dump_json()
        and "Invented missing" not in receipts[0].model_dump_json()
    )
    with session_for(engine) as session:
        assert session.get(
            Patient, TypeID.from_string(visit["patient_ref"])
        ).date_of_birth == datetime(1980, 2, 3)


@pytest.mark.parametrize("changed", ["date", "patient", "visit"])
def test_retained_dob_key_cannot_change_payload(engine, workflow, changed):
    visit, update, invoke = missing_visit(workflow)
    invoke(update)
    _, _, request, _ = workflow
    other = invoke(
        request(
            patient={
                "kind": "new",
                "name": "Invented second patient",
                "date_of_birth": None,
            }
        )
    )["encounter"]
    values = (
        {"date_of_birth": "1981-02-03"}
        if changed == "date"
        else {
            "appointment_ref": other["appointment_ref"],
            "patient_ref": other["patient_ref"],
        }
        if changed == "visit"
        else {"patient_ref": other["patient_ref"]}
    )
    altered = request(
        **{**update.model_dump(mode="json"), **values, "request_nonce": str(uuid4())}
    )
    with pytest.raises(
        (scribe_encounters.EncounterConflict, authority.AuthorityRequestDeniedError)
    ):
        invoke(altered)
    assert len(audit_receipts(engine, update.change_key)) == 1
    with session_for(engine) as session:
        assert session.get(
            Patient, TypeID.from_string(visit["patient_ref"])
        ).date_of_birth == datetime(1980, 2, 3)
        assert (
            session.get(Patient, TypeID.from_string(other["patient_ref"])).date_of_birth
            is None
        )


def test_new_key_does_not_overwrite_known_birth_date(engine, workflow):
    visit, update, invoke = missing_visit(workflow)
    invoke(update)
    changed = update.model_copy(
        update={"change_key": uuid4(), "date_of_birth": datetime(1981, 3, 4).date()}
    )
    with pytest.raises(scribe_encounters.EncounterConflict):
        invoke(changed)
    assert not audit_receipts(engine, changed.change_key)
    with session_for(engine) as session:
        assert session.get(
            Patient, TypeID.from_string(visit["patient_ref"])
        ).date_of_birth == datetime(1980, 2, 3)


@pytest.mark.parametrize(
    "field,value",
    [
        ("appointment_ref", "appointment_" + "0" * 26),
        ("patient_ref", "patient_" + "0" * 26),
        ("expected_actor_ref", "authority_actor_" + "0" * 26),
        ("expected_tenant_ref", "authority_tenant_" + "0" * 26),
    ],
)
def test_unmatched_or_foreign_scope_cannot_update_dob(engine, workflow, field, value):
    visit, update, invoke = missing_visit(workflow)
    with pytest.raises(authority.AuthorityKernelError):
        invoke(update.model_copy(update={field: value}))
    assert not audit_receipts(engine, update.change_key)
    with session_for(engine) as session:
        assert (
            session.get(Patient, TypeID.from_string(visit["patient_ref"])).date_of_birth
            is None
        )


@pytest.mark.parametrize("birth_date", ["1899-12-31", "2999-01-01"])
def test_add_dob_range_checks_leave_unknown_data_untouched(
    engine, workflow, birth_date
):
    visit, update, invoke = missing_visit(workflow)
    _, _, request, _ = workflow
    changed = request(**{**update.model_dump(mode="json"), "date_of_birth": birth_date})
    with pytest.raises(ValueError, match="birth_date_out_of_range"):
        invoke(changed)
    assert not audit_receipts(engine, update.change_key)
    with session_for(engine) as session:
        assert (
            session.get(Patient, TypeID.from_string(visit["patient_ref"])).date_of_birth
            is None
        )
