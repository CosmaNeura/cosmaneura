"""Real PG catalog batching: current authority, cardinality and contention."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime

import psycopg
import pytest
from typeid import TypeID

from app.services import authority, scribe_catalog_rows, scribe_encounters

from app.models.authority import (
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
)
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from app.models.scribe_encounter_creation import ScribeEncounterCreation
from app.models.scribe_patient_identity import ScribePatientIdentity
from sqlalchemy import delete, text
from sqlalchemy.exc import MultipleResultsFound, OperationalError
from sqlmodel import select

from tests.authority_kernel.companion_provider_provisioning_test import session_for
from tests.authority_kernel.scribe_encounters_test import count
from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_encounters_test import workflow as _workflow

engine, workflow = _engine, _workflow


def _list(invoke, request, monkeypatch, *, batched):
    with monkeypatch.context() as patch:
        if not batched:
            patch.setattr(
                scribe_catalog_rows,
                "catalog_rows",
                lambda *_args, **_kwargs: nullcontext(),
            )
        return invoke(request("list"))["encounters"]


def test_catalog_preserves_provisional_canonical_identity_and_order(
    engine, workflow, monkeypatch
):
    _, provisioned, request, invoke = workflow
    first = invoke(
        request(
            patient={"kind": "new", "name": "Invented provisional", "provisional": True}
        )
    )["encounter"]
    second = invoke(request())["encounter"]
    with session_for(engine) as session:
        identity = session.exec(
            select(ScribePatientIdentity).where(
                ScribePatientIdentity.appointment_id
                == TypeID.from_string(first["appointment_ref"])
            )
        ).one()
        identity.canonical_patient_id = TypeID.from_string(second["patient_ref"])
        identity.state, identity.version = "matched", 1
        session.add(identity)
        session.commit()
    old = _list(invoke, request, monkeypatch, batched=False)
    new = _list(invoke, request, monkeypatch, batched=True)
    assert new == old
    matched = next(v for v in new if v["appointment_ref"] == first["appointment_ref"])
    assert matched["identity"]["canonical_patient_ref"] == second["patient_ref"]
    assert matched["patient_ref"] == first["patient_ref"]
    assert scribe_catalog_rows._SCOPE.get() is None
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "fault",
    [
        "record_missing",
        "record_patient",
        "record_doctor",
        "binding_missing",
        "binding_revoked",
        "binding_actor",
        "person_revoked",
        "origin_missing",
        "origin_duplicate",
        "capture_revoked",
        "capture_workspace",
        "identity_source",
        "identity_tenant",
    ],
)
def test_catalog_keeps_original_missing_foreign_revoked_and_duplicate_refusals(
    engine, workflow, monkeypatch, fault
):
    _, provisioned, request, invoke = workflow
    first = invoke(
        request(
            patient={"kind": "new", "name": "Invented provisional", "provisional": True}
        )
    )["encounter"]
    second = invoke(request())["encounter"]
    with session_for(engine) as session:
        row = session.exec(
            select(ScribeEncounterCreation).where(
                ScribeEncounterCreation.appointment_id
                == TypeID.from_string(first["appointment_ref"])
            )
        ).one()
        link = session.get(ScribeCaptureProviderLink, provisioned.provider_link_ref)
        binding = session.exec(
            select(AuthorityPersonBinding).where(
                AuthorityPersonBinding.binding_ref == first["appointment_ref"]
            )
        ).one()
        record = session.exec(
            select(ProviderRecordAuthority).where(
                ProviderRecordAuthority.binding_id == binding.id
            )
        ).one()
        origin = session.exec(
            select(AuthorityPersonOrigin).where(
                AuthorityPersonOrigin.person_id == binding.person_id,
                AuthorityPersonOrigin.source_ref == first["patient_ref"],
            )
        ).one()
        identity = session.exec(
            select(ScribePatientIdentity).where(
                ScribePatientIdentity.appointment_id == row.appointment_id
            )
        ).one()
        if fault == "record_missing":
            session.delete(record)
        elif fault == "record_patient":
            record.patient_id = TypeID.from_string(second["patient_ref"])
            session.add(record)
        elif fault == "record_doctor":
            from uuid import uuid4

            from app.models.doctor import Doctor

            other = Doctor(
                clerk_id="user_synthetic_" + uuid4().hex,
                email=uuid4().hex + "@synthetic.invalid",
                last_active_at=datetime.now(UTC),
                biography=None,
            )
            session.add(other)
            session.flush()
            record.doctor_id = other.id
            session.add(record)
        elif fault == "binding_missing":
            binding.binding_ref = "synthetic_missing_" + str(binding.id)
            session.add(binding)
        elif fault == "binding_revoked":
            binding.revoked_at = datetime.now(UTC)
            session.add(binding)
        elif fault == "binding_actor":
            binding.actor_id = None
            session.add(binding)
        elif fault == "person_revoked":
            person = session.get(AuthorityPerson, binding.person_id)
            person.status = "revoked"
            session.add(person)
        elif fault == "origin_missing":
            # Deliberately corrupt only this fresh synthetic schema; normal ORM
            # provenance is append-only and must retain its deletion guard.
            session.exec(
                delete(AuthorityPersonOrigin).where(
                    AuthorityPersonOrigin.id == origin.id
                )
            )
        elif fault == "origin_duplicate":
            session.add(
                AuthorityPersonOrigin(
                    person_id=origin.person_id,
                    origin=origin.origin,
                    source_ref=origin.source_ref,
                )
            )
        elif fault.startswith("capture_"):
            from uuid import uuid4

            session.add(
                ScribeCaptureBinding(
                    workspace_id=uuid4()
                    if fault == "capture_workspace"
                    else row.workspace_id,
                    provider_link_id=link.id,
                    actor_id=link.actor_id,
                    tenant_id=link.tenant_id,
                    source_identity_id=link.source_identity_id,
                    person_binding_id=binding.id,
                    doctor_id=link.doctor_id,
                    practice_id=link.practice_id,
                    patient_id=row.patient_id,
                    appointment_id=row.appointment_id,
                    scribe_tenant_id=link.scribe_tenant_id,
                    scope_sha256="a" * 64,
                    revoked_at=datetime.now(UTC)
                    if fault == "capture_revoked"
                    else None,
                )
            )
        elif fault == "identity_source":
            identity.source_patient_id = TypeID.from_string(second["patient_ref"])
            session.add(identity)
        else:
            from app.models.authority import AuthorityTenant

            foreign = AuthorityTenant(status="active")
            session.add(foreign)
            session.flush()
            identity.tenant_id = foreign.id
            session.add(identity)
        session.commit()
    outcomes = []
    for batched in (False, True):
        try:
            _list(invoke, request, monkeypatch, batched=batched)
        except (
            authority.AuthorityRequestDeniedError,
            ValueError,
            MultipleResultsFound,
        ) as error:
            outcomes.append(type(error))
        else:
            pytest.fail("Malformed catalog row was published")
    assert outcomes[0] is outcomes[1]
    assert scribe_catalog_rows._SCOPE.get() is None
    assert engine.pool.checkedout() == 0


def test_all_identity_rows_are_locked_before_projection(engine, workflow, monkeypatch):
    _, _, request, invoke = workflow
    visits = [
        invoke(
            request(
                patient={
                    "kind": "new",
                    "name": "Invented identity",
                    "provisional": True,
                }
            )
        )["encounter"]
        for _ in range(3)
    ]
    expected = _list(invoke, request, monkeypatch, batched=False)
    reading, release = threading.Event(), threading.Event()
    first = True
    actual = scribe_encounters._visit

    def held(*args, **kwargs):
        nonlocal first
        value = actual(*args, **kwargs)
        if first:
            first = False
            reading.set()
            assert release.wait(5)
        return value

    monkeypatch.setattr(scribe_encounters, "_visit", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke, request("list"))
        try:
            assert reading.wait(5)
            for visit in visits:
                with session_for(engine) as writer:
                    writer.exec(text("SET LOCAL lock_timeout='100ms'"))
                    with pytest.raises(OperationalError) as blocked:
                        writer.exec(
                            select(ScribePatientIdentity)
                            .where(
                                ScribePatientIdentity.appointment_id
                                == TypeID.from_string(visit["appointment_ref"])
                            )
                            .with_for_update()
                        ).one()
                    assert blocked.value.orig.sqlstate == "55P03"
        finally:
            release.set()
        assert future.result(timeout=5)["encounters"] == expected


@pytest.mark.parametrize("contender_action", ["list", "create"])
def test_eight_visit_catalog_reduces_wire_calls_and_preserves_concurrent_create(
    engine, workflow, monkeypatch, record_property, contender_action
):
    _, _, request, invoke = workflow
    first = invoke(request())["encounter"]
    for _ in range(7):
        invoke(
            request(patient={"kind": "existing", "patient_ref": first["patient_ref"]})
        )
    local = threading.local()
    acquired = threading.Event()
    runs = []
    execute = psycopg.Cursor.execute

    def observed(cursor, query, *args, **kwargs):
        result = execute(cursor, query, *args, **kwargs)
        run = getattr(local, "run", None)
        if run is not None:
            run["wire_calls"] += 1
            shape = " ".join(query.lower().split()) if isinstance(query, str) else ""
            if "set_config" in shape:
                run["timeout_setters"] += 1
            if (
                "from practice" in shape
                and "for update" in shape
                and "first_lock" not in run
            ):
                run["first_lock"] = time.monotonic()
                acquired.set()
            time.sleep(0.015)  # Controlled per-execute transport delay; not a live SLA.
        return result

    monkeypatch.setattr(psycopg.Cursor, "execute", observed)
    original_scope = scribe_catalog_rows.catalog_rows
    for batched in (False, True):
        acquired.clear()
        run = {"wire_calls": 0, "timeout_setters": 0, "batched": batched}

        def catalog():
            local.run = run
            try:
                return invoke(request("list"))
            finally:
                run["finished"] = time.monotonic()
                local.run = None

        value = request(contender_action)
        before = count(engine, ScribeEncounterCreation)
        with monkeypatch.context() as patch:
            if not batched:
                patch.setattr(
                    scribe_catalog_rows,
                    "catalog_rows",
                    lambda *_args, **_kwargs: nullcontext(),
                )
            else:
                patch.setattr(scribe_catalog_rows, "catalog_rows", original_scope)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(catalog)
                assert acquired.wait(5)
                if not batched:
                    with pytest.raises(OperationalError) as blocked:
                        invoke(value)
                    assert blocked.value.orig.sqlstate == "55P03"
                else:
                    answer = invoke(value)
                    assert answer["action"] == contender_action
                assert len(future.result(timeout=15)["encounters"]) == 8
        assert count(engine, ScribeEncounterCreation) == before + (
            batched and contender_action == "create"
        )
        run["lock_hold_ms"] = (run["finished"] - run["first_lock"]) * 1000
        runs.append(run)
    assert runs[1]["wire_calls"] < runs[0]["wire_calls"] * 0.55
    assert runs[0]["lock_hold_ms"] > 2000
    record_property("controlled_transport_delay_ms", 15)
    record_property("wire_runs", runs)
    assert engine.pool.checkedout() == 0


def test_lookup_and_create_do_not_enter_catalog_batch(workflow, monkeypatch):
    _, _, request, invoke = workflow

    def forbidden(*_args, **_kwargs):
        pytest.fail("Non-catalog request entered batching")

    monkeypatch.setattr(scribe_catalog_rows, "catalog_rows", forbidden)
    created = invoke(request())["encounter"]
    assert (
        invoke(request("lookup", workspace_id=created["workspace_id"]))["encounter"]
        == created
    )


def test_final_catalog_check_rejects_entitlement_expired_during_projection(
    engine, workflow, monkeypatch
):
    from datetime import timedelta

    from app.services import doctor_access

    from app.models.practice import Practice

    _, provisioned, request, invoke = workflow
    invoke(request())
    now = datetime.now(UTC)
    with session_for(engine) as session:
        link = session.get(ScribeCaptureProviderLink, provisioned.provider_link_ref)
        practice = session.get(Practice, link.practice_id)
        practice.billing_exempt_until = now + timedelta(minutes=1)
        practice.trial_ends_at = None
        session.add(practice)
        session.commit()
    actual = scribe_encounters._visit

    class Expired(datetime):
        @classmethod
        def now(cls, tz=None):
            return (now + timedelta(minutes=2)).astimezone(tz)

    def expires(*args, **kwargs):
        value = actual(*args, **kwargs)
        monkeypatch.setattr(doctor_access, "datetime", Expired)
        return value

    monkeypatch.setattr(scribe_encounters, "_visit", expires)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request("list"))
    assert scribe_catalog_rows._SCOPE.get() is None
