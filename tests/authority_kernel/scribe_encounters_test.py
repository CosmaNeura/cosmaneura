"""Repeat visits and current authorization on an explicitly owned local PG DB."""

import importlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from companion_provider_provisioning_test import (
    SETTINGS,
    apply,
    case,
    count,
    plan,
    session_for,
)
from typeid import TypeID

from app.configuration import session_prep
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.services import (
    authority,
    provider_record_authority,
    scribe_capture_scope,
    scribe_encounters,
    view_as,
)
from app.services.companion_records import RecordBudget
from app.services.scribe_encounter_contract import REQUEST_ADAPTER

from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from app.models.scribe_encounter_creation import ScribeEncounterCreation
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine

REAL_RECORD_GUARD = provider_record_authority.validate_provider_record_scope
REAL_VIEW_AS_GUARD = view_as.mount_context_session_blocked_by_view_as


@pytest.fixture(scope="module")
def engine():
    raw = os.environ.get("SCRIBE_ENCOUNTER_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit owned local PostgreSQL required")
    url = make_url(raw)
    original_scope = (
        url.database
        in {"scribe_encounter_workflow_test", "scribe_optional_dob_workflow_test"}
        and url.host is None
        and url.query.get("host")
        == "/private/tmp/scribe-provider-receiver-opening-20260928/socket"
    )
    ux_scope = (url.database, url.host, url.port, url.username) == (
        "core_ux_01_test",
        "127.0.0.1",
        55930,
        "scribe_ux_lab_owner",
    )
    desktop_scope = (url.database, url.host, url.port, url.username) == (
        "core_ux_01_test",
        "127.0.0.1",
        55937,
        "scribe_ux_lab_owner",
    )
    catalog_scope = (
        (url.database, url.host, url.username)
        == ("scribe_configured_core_test", None, "scribe_configured_owner")
        and url.query.get("host")
        == "/private/tmp/scribe-configured-core.retention-20261025"
        and url.query.get("port") == "55458"
        and re.fullmatch(
            r"-csearch_path=catalog_scope_[0-9a-f]{32}",
            str(url.query.get("options", "")),
        )
        is not None
    )
    if catalog_scope:
        assert json.loads(
            Path(
                "/private/tmp/scribe-configured-core.retention-20261025/owner.json"
            ).read_text()
        ) == {
            "task": "virtual-lab-retention-20261025",
            "synthetic_only": True,
            "listen_addresses": "",
            "socket": "/private/tmp/scribe-configured-core.retention-20261025",
            "port": 55458,
        }
    if desktop_scope:
        marker = Path("/private/tmp/scribe-ux-latency-lab-20261007/ownership.json")
        assert json.loads(marker.read_text()) == {
            "created_for": "scribe-desktop-ux-20261007",
            "host": "127.0.0.1",
            "port": 55937,
            "database": "core_ux_01_test",
            "username": "scribe_ux_lab_owner",
            "only_synthetic": True,
        }
    assert original_scope or ux_scope or desktop_scope or catalog_scope
    for source in (Path(__file__).parents[2] / "app/models").glob("*.py"):
        if source.stem != "__init__":
            importlib.import_module("app.models." + source.stem)
    result = create_engine(url)
    SQLModel.metadata.create_all(result)
    yield result
    result.dispose()


@pytest.fixture
def workflow(engine, monkeypatch):
    monkeypatch.setattr(
        scribe_encounters, "global_session", lambda: session_for(engine)
    )
    monkeypatch.setattr(authority, "validate_provider_record_scope", REAL_RECORD_GUARD)
    monkeypatch.setattr(
        view_as, "mount_context_session_blocked_by_view_as", REAL_VIEW_AS_GUARD
    )
    args = case(engine)
    monkeypatch.setattr(
        session_prep,
        "SESSION_PREP_PROVIDER_SETTINGS",
        session_prep.SessionPrepProviderSettings(enabled=True),
    )
    provisioned = apply(engine, args, plan(engine, args).plan_sha256)
    with session_for(engine) as session:
        stored_scribe_capture_provider_link = session.get(
            ScribeCaptureProviderLink, provisioned.provider_link_ref
        )
        assert stored_scribe_capture_provider_link is not None
        actor_ref = str(stored_scribe_capture_provider_link.actor_id)

    def request(action="create", **values):
        body: dict[str, object] = dict(
            action=action,
            expected_actor_ref=actor_ref,
            expected_tenant_ref=str(args["tenant_id"]),
            request_nonce=str(uuid4()),
        )
        if action == "create":
            body.update(
                creation_key=str(uuid4()),
                appointment_at=datetime.now(UTC).isoformat(),
                patient={
                    "kind": "new",
                    "name": "Invented Scribe Patient",
                    "date_of_birth": "1990-01-01",
                },
            )
        return REQUEST_ADAPTER.validate_python(body | values)

    def invoke(value, deadline=None):
        return scribe_encounters.encounter_workflow(
            issuer=SETTINGS.clerk_issuer,
            subject=args["verified_clerk_subject"],
            session_id="session_invented_scribe",
            request=value,
            settings_provider=lambda: ScribeEncounterSettings(enabled=True),
            authority_settings=SETTINGS,
            deadline=deadline or RecordBudget(time.monotonic() + 15),
        )

    return args, provisioned, request, invoke


def test_three_visits_reuse_exact_patient_and_retry_without_duplicates(
    engine, workflow
):
    _, _, request, invoke = workflow
    before = [count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)]
    first_request = request()
    first = invoke(first_request)
    # Lost HTTP response: only correlation nonce changes, retained key/body reuse.
    retry = invoke(first_request.model_copy(update={"request_nonce": uuid4()}))
    assert retry["encounter"] == first["encounter"]
    patient = first["encounter"]["patient_ref"]
    second = invoke(request(patient={"kind": "existing", "patient_ref": patient}))
    third = invoke(request(patient={"kind": "existing", "patient_ref": patient}))
    expected = [v["encounter"] for v in (first, second, third)]
    assert len({v["workspace_id"] for v in expected}) == 3
    assert len({v["appointment_ref"] for v in expected}) == 3
    listing = invoke(request("list"))["encounters"]
    assert {v["appointment_ref"] for v in listing} == {
        v["appointment_ref"] for v in expected
    }
    for visit in expected:
        assert (
            invoke(request("lookup", workspace_id=visit["workspace_id"]))["encounter"]
            == visit
        )
    after = [count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)]
    assert [b - a for a, b in zip(before, after, strict=True)] == [1, 3, 3]


def test_simultaneous_double_click_creates_once(engine, workflow):
    _, _, request, invoke = workflow
    value = request()
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return invoke(value)

    before = count(engine, ScribeEncounterCreation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: run(), range(2)))
    assert a["encounter"] == b["encounter"]
    assert count(engine, ScribeEncounterCreation) == before + 1


@pytest.mark.parametrize("birth_date", [{}, {"date_of_birth": None}])
def test_unknown_birth_date_persists_and_retries_same_patient(
    engine, workflow, birth_date
):
    _, _, request, invoke = workflow
    before = [count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)]
    value = request(
        patient={"kind": "new", "name": "Invented unknown birthday", **birth_date}
    )
    first = invoke(value)["encounter"]
    # Omitted and explicit null have one canonical intent, including a lost reply.
    retry = request(
        **{
            **value.model_dump(mode="json"),
            "request_nonce": str(uuid4()),
            "patient": {
                "kind": "new",
                "name": "Invented unknown birthday",
                "date_of_birth": None,
            },
        }
    )
    assert invoke(retry)["encounter"] == first
    with session_for(engine) as session:
        stored_patient = session.get(Patient, TypeID.from_string(first["patient_ref"]))
        assert stored_patient is not None
        assert stored_patient.date_of_birth is None
    patients = invoke(request("patients", query="Invented unknown birthday"))[
        "patients"
    ]
    assert patients == [
        {
            "patient_ref": first["patient_ref"],
            "name": "Invented unknown birthday",
            "date_of_birth": None,
        }
    ]
    next_visit = invoke(
        request(patient={"kind": "existing", "patient_ref": first["patient_ref"]})
    )["encounter"]
    assert next_visit["patient_ref"] == first["patient_ref"]
    assert next_visit["appointment_ref"] != first["appointment_ref"]
    after = [count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)]
    assert [b - a for a, b in zip(before, after, strict=True)] == [1, 2, 2]


@pytest.mark.parametrize("date_of_birth", ["1899-12-31", "2999-01-01"])
def test_provided_birth_date_must_remain_in_range(engine, workflow, date_of_birth):
    _, _, request, invoke = workflow
    before = [count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)]
    with pytest.raises(ValueError, match="birth_date_out_of_range"):
        invoke(
            request(
                patient={
                    "kind": "new",
                    "name": "Invented invalid birthday",
                    "date_of_birth": date_of_birth,
                }
            )
        )
    assert [
        count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)
    ] == before


def test_reusing_key_for_different_details_conflicts(workflow):
    _, _, request, invoke = workflow
    value = request()
    invoke(value)
    with pytest.raises(scribe_encounters.EncounterConflict):
        invoke(value.model_copy(update={"title": "Different visit"}))


def test_same_name_is_never_an_identity_match(workflow):
    _, _, request, invoke = workflow
    first, second = invoke(request()), invoke(request())
    assert first["encounter"]["patient_ref"] != second["encounter"]["patient_ref"]


def test_foreign_patient_and_workspace_are_denied(engine, workflow):
    _, _, request, invoke = workflow
    other = case(engine)
    with session_for(engine) as session:
        patient = Patient(
            doctor_id=other["doctor_id"],
            name="Other invented patient",
            date_of_birth=datetime(1990, 1, 1),
        )
        session.add(patient)
        session.commit()
        patient_ref = str(patient.id)
    before = count(engine, ScribeEncounterCreation)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request(patient={"kind": "existing", "patient_ref": patient_ref}))
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request("lookup", workspace_id=str(uuid4())))
    assert count(engine, ScribeEncounterCreation) == before


def test_revocation_denies_retry_and_listing(engine, workflow):
    _, provisioned, request, invoke = workflow
    value = request()
    invoke(value)
    with session_for(engine) as session:
        link = session.get(ScribeCaptureProviderLink, provisioned.provider_link_ref)
        assert link is not None
        link.revoked_at = datetime.now(UTC)
        session.add(link)
        session.commit()
    for value in (value, request("list"), request("patients")):
        with pytest.raises(authority.AuthorityRequestDeniedError):
            invoke(value)


def test_support_view_blocks_creation_without_writes(engine, workflow, monkeypatch):
    from app.services import companion_encounters

    _, _, request, invoke = workflow
    monkeypatch.setattr(
        companion_encounters, "actor_bound_unended_view_as_exists", lambda *a, **k: True
    )
    before = count(engine, Patient)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request())
    assert count(engine, Patient) == before


def test_failure_after_patient_insert_rolls_back_everything(
    engine, workflow, monkeypatch
):
    _, _, request, invoke = workflow

    def fail(**_):
        raise RuntimeError("injected_source_binding_failure")

    monkeypatch.setattr(provider_record_authority, "ensure_provider_record_scope", fail)
    before = [count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)]
    with pytest.raises(RuntimeError):
        invoke(request())
    assert [
        count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)
    ] == before


def test_patient_search_stays_in_account_and_returns_exact_ids(engine, workflow):
    args, _, request, invoke = workflow
    created = invoke(request())
    result = invoke(request("patients", query="Invented Scribe"))
    assert result["patients"] == [
        {
            "patient_ref": created["encounter"]["patient_ref"],
            "name": "Invented Scribe Patient",
            "date_of_birth": "1990-01-01",
        }
    ]
    assert result["account"]["doctor_ref"] == str(args["doctor_id"])


def test_created_visit_enters_existing_capture_scope_without_fixture_repairs(
    engine, workflow
):
    args, _, request, invoke = workflow
    visit = invoke(request())["encounter"]
    with session_for(engine) as session:
        token = authority.exchange_context_token(
            clerk_issuer=SETTINGS.clerk_issuer,
            clerk_subject=args["verified_clerk_subject"],
            requested_surface="provider_assistant",
            record_ref=visit["appointment_ref"],
            session=session,
            settings=SETTINGS,
        )
        workload, turn = str(uuid4()), uuid4()
        resolved = authority.resolve_mount_context(
            workload_client_id=workload,
            mount_context_token=token.context_token,
            client_turn_id=turn,
            session=session,
            settings=SETTINGS,
        )
        session.commit()
        from uuid import UUID

        capture = scribe_capture_scope.resolve_capture_binding(
            workload_client_id=workload,
            mount_context_token=token.context_token,
            interaction_ref=resolved.interaction_ref,
            client_turn_id=turn,
            workspace_id=UUID(visit["workspace_id"]),
            appointment_ref=visit["appointment_ref"],
            request_nonce=uuid4(),
            settings=SETTINGS,
            capture_settings=ScribeCaptureSettings(enabled=True),
        )
    assert capture.appointment_ref == visit["appointment_ref"]
    assert (
        invoke(request("lookup", workspace_id=visit["workspace_id"]))["encounter"]
        == visit
    )


def test_cancel_after_source_insertion_rolls_back(engine, workflow, monkeypatch):
    _, _, request, invoke = workflow
    budget = RecordBudget(time.monotonic() + 15)
    original = provider_record_authority.ensure_provider_record_scope

    def cancel(**kwargs):
        result = original(**kwargs)
        budget.cancel()
        return result

    monkeypatch.setattr(
        provider_record_authority, "ensure_provider_record_scope", cancel
    )
    before = [count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)]
    from app.services.companion_records import RecordDeadline

    with pytest.raises(RecordDeadline):
        invoke(request(), budget)
    assert [
        count(engine, m) for m in (Patient, Appointment, ScribeEncounterCreation)
    ] == before
