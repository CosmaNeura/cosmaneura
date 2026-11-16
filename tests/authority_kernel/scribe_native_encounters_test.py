"""Offline native visit boundary and exact existing writer; synthetic values."""

import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typeid import TypeID

from app.configuration import scribe_native
from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_native_encounters as route
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
)
from app.services import authority, scribe_encounters
from app.services import scribe_native_encounters as service
from app.services.companion_encounter_contract import (
    ConnectionContent,
    ConnectionSnapshot,
    digest,
)
from app.services.scribe_encounter_contract import CreateRequest, PatientsRequest

ACTOR = "authority_actor_00000000000000000000000001"
TENANT = "authority_tenant_0000000000e008000000000002"
PATIENT = "patient_00000000000000000000000003"
ISSUER = "https://synthetic.clerk.invalid"
AUTH = AuthorityKernelSettings(True, 900, 300, clerk_issuer=ISSUER)


def connection():
    content = ConnectionContent(
        actor_ref=ACTOR,
        tenant_ref=TENANT,
        provider_link_ref="scribe_provider_link_00000000000000000000000004",
        practice_ref="practice_00000000000000000000000005",
        practice_label="Invented practice",
        review_sha256="a" * 64,
    ).model_dump(mode="json")
    return ConnectionSnapshot(**content, connection_sha256=digest(content))


def creation(**updates):
    body = dict(
        action="create",
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        request_nonce=str(uuid4()),
        creation_key=str(uuid4()),
        patient=dict(kind="existing", patient_ref=PATIENT),
        appointment_at=datetime.now(UTC).isoformat(),
        title="Visit",
    )
    return CreateRequest.model_validate(body | updates)


def invoke(request, enabled=True):
    return service.execute_native_encounter(
        issuer=ISSUER,
        subject="user_synthetic",
        session_id="sess_synthetic",
        request=request,
        authority_settings=AUTH,
        settings_provider=lambda: ScribeEncounterSettings(enabled),
        deadline=time.monotonic() + 10,
    )


def response(request):
    return dict(
        version="1",
        action=request.action,
        request_nonce=str(request.request_nonce),
        account=dict(actor_ref=ACTOR, tenant_ref=TENANT),
        **(
            {"creation_key": str(request.creation_key), "encounter": {}}
            if isinstance(request, CreateRequest)
            else {"patients": [], "more_results": False}
        ),
    )


def test_existing_visit_reuses_exact_writer_and_checks_current_context(monkeypatch):
    calls = []

    def current(**kwargs):
        calls.append("current")
        assert kwargs["session_id"] == "sess_synthetic"
        return connection()

    monkeypatch.setattr(service.scribe_provider_context, "_connection", current)
    request = creation()

    def writer(**kwargs):
        calls.append("writer")
        assert kwargs["request"] is request
        assert kwargs["settings_provider"]().enabled
        assert kwargs["authority_settings"] is AUTH
        return response(request)

    monkeypatch.setattr(scribe_encounters, "encounter_workflow", writer)
    assert invoke(request) == response(request)
    assert calls == ["current", "writer", "current"]


@pytest.mark.parametrize(
    "change", ["disabled", "foreign_actor", "foreign_tenant", "new_patient"]
)
def test_closed_subset_denies_before_writer(monkeypatch, change):
    calls = []
    monkeypatch.setattr(
        service.scribe_provider_context, "_connection", lambda **_: connection()
    )
    monkeypatch.setattr(
        scribe_encounters, "encounter_workflow", lambda **_: calls.append("write")
    )
    request = creation()
    if change == "foreign_actor":
        request = creation(
            expected_actor_ref="authority_actor_00000000000000000000000009"
        )
    if change == "foreign_tenant":
        request = creation(
            expected_tenant_ref="authority_tenant_00000000000000000000000009"
        )
    if change == "new_patient":
        request = creation(patient=dict(kind="new", name="Invented patient"))
    with pytest.raises(
        (authority.AuthorityRequestDeniedError, service.RecordUnavailable, ValueError)
    ):
        invoke(request, enabled=change != "disabled")
    assert calls == []


def test_changed_current_authority_after_writer_does_not_return_success(monkeypatch):
    reads = iter(
        [connection(), connection().model_copy(update={"review_sha256": "b" * 64})]
    )
    monkeypatch.setattr(
        service.scribe_provider_context, "_connection", lambda **_: next(reads)
    )
    monkeypatch.setattr(
        scribe_encounters, "encounter_workflow", lambda **kw: response(kw["request"])
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(creation())


def test_existing_writer_same_key_and_new_nonce_reuses_receipt_and_changed_intent_conflicts():
    request = creation()
    row = SimpleNamespace(
        request_sha256=digest(
            request.model_dump(mode="json", exclude={"request_nonce"})
        )
    )
    session = SimpleNamespace(exec=lambda _: SimpleNamespace(one_or_none=lambda: row))
    assert scribe_encounters._create(
        session, request, "doctor_synthetic", None, ISSUER, "user_synthetic"
    ) == (row, False)
    assert scribe_encounters._create(
        session,
        request.model_copy(update={"request_nonce": uuid4()}),
        "doctor_synthetic",
        None,
        ISSUER,
        "user_synthetic",
    ) == (row, False)
    with pytest.raises(scribe_encounters.EncounterConflict):
        scribe_encounters._create(
            session,
            request.model_copy(update={"title": "Different visit"}),
            "doctor_synthetic",
            None,
            ISSUER,
            "user_synthetic",
        )


@pytest.mark.parametrize(
    "membership", ["foreign", "inactive_kind", "valid_but_foreign_patient"]
)
def test_actual_writer_requires_current_practice_member_and_doctor_owned_patient(
    membership,
):
    request = creation()
    link = SimpleNamespace(
        practice_id=TypeID.from_string("practice_00000000000000000000000005"),
        actor_id=TypeID.from_string(ACTOR),
        tenant_id=TypeID.from_string(TENANT),
    )
    queries = []
    values = iter(
        [
            None,
            SimpleNamespace(tenant_id=link.tenant_id),
            [
                SimpleNamespace(
                    tenant_id=link.tenant_id if membership != "foreign" else "other",
                    membership_kind="provider"
                    if membership != "inactive_kind"
                    else "support",
                )
            ],
            None,
        ]
    )

    def execute(statement):
        queries.append(str(statement))
        value = next(values)
        return (
            value
            if isinstance(value, list)
            else SimpleNamespace(one_or_none=lambda: value)
        )

    session = SimpleNamespace(exec=execute)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        scribe_encounters._create(
            session,
            request,
            TypeID.from_string("doctor_00000000000000000000000006"),
            link,
            ISSUER,
            "user_synthetic",
        )
    if membership == "valid_but_foreign_patient":
        assert (
            len(queries) == 4
            and "patient.doctor_id" in queries[-1]
            and "patient.id NOT IN" in queries[-1]
        )
    else:
        assert len(queries) == 3


def client(monkeypatch, enabled=True):
    app = FastAPI()
    route.install(app)
    app.dependency_overrides[route.authenticate_native_workload] = (
        lambda: EntraServicePrincipal("11111111-1111-4111-8111-111111111111")
    )
    app.dependency_overrides[authenticate_embed_platform_session] = (
        lambda: VerifiedClerkIdentity(ISSUER, "user_synthetic", "sess_synthetic")
    )
    app.dependency_overrides[route.native_existing_patient_visits_enabled] = (
        lambda: enabled
    )
    app.dependency_overrides[route.get_authority_kernel_settings] = lambda: replace(
        AUTH, enabled=False
    )
    monkeypatch.setattr(
        scribe_native,
        "SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS",
        SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(
        scribe_native, "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED", True
    )
    calls = []

    def execute(**kw):
        calls.append(kw)
        assert kw["authority_settings"].enabled and kw["settings_provider"]().enabled
        return response(kw["request"])

    monkeypatch.setattr(route, "execute_native_encounter", execute)
    return TestClient(app), calls, app


def headers(request):
    return {
        "Authorization": "Bearer synthetic-human",
        "X-Cosmaneura-Workload-Authorization": "Bearer synthetic-workload",
        "X-Cosmaneura-Scribe-Encounter-Version": "1",
        "Idempotency-Key": str(request.creation_key),
    }


def test_native_route_enabled_preserves_key_human_and_native_only_authority(
    monkeypatch,
):
    global_settings = scribe_native.SCRIBE_ENCOUNTER_SETTINGS
    http, calls, _ = client(monkeypatch)
    request = creation()
    result = http.post(
        route.PATH, json=request.model_dump(mode="json"), headers=headers(request)
    )
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["creation_key"] == str(request.creation_key)
    assert len(calls) == 1 and calls[0]["session_id"] == "sess_synthetic"
    assert (
        scribe_native.SCRIBE_ENCOUNTER_SETTINGS is global_settings
        and not global_settings.enabled
    )


def test_dark_native_selection_is_same_original_encounter_settings(monkeypatch):
    original = scribe_native.SCRIBE_ENCOUNTER_SETTINGS
    monkeypatch.setattr(
        scribe_native, "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED", False
    )
    assert scribe_native.native_encounter_settings() is original
    http, calls, _ = client(monkeypatch, False)
    request = creation()
    assert (
        http.post(
            route.PATH, json=request.model_dump(mode="json"), headers=headers(request)
        ).status_code
        == 503
    )
    assert calls == []


def test_fresh_native_gate_cannot_fall_back_to_enabled_browser_gate(monkeypatch):
    http, _, _ = client(monkeypatch)
    monkeypatch.setattr(
        scribe_native, "SCRIBE_ENCOUNTER_SETTINGS", ScribeEncounterSettings(True)
    )
    checked = []

    def execute(**kwargs):
        settings = kwargs["settings_provider"]
        assert settings().enabled
        monkeypatch.setattr(
            scribe_native, "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED", False
        )
        checked.append(settings().enabled)
        raise service.RecordUnavailable

    monkeypatch.setattr(route, "execute_native_encounter", execute)
    request = creation()
    result = http.post(
        route.PATH, json=request.model_dump(mode="json"), headers=headers(request)
    )
    assert result.status_code == 503 and checked == [False]


@pytest.mark.parametrize(
    "bad",
    [
        "key",
        "new_patient",
        "cookie",
        "missing_workload",
        "missing_human",
        "version",
        "private_note",
    ],
)
def test_native_route_rejects_unsupported_or_unbound_operation(monkeypatch, bad):
    http, calls, app = client(monkeypatch)
    request = creation()
    body = request.model_dump(mode="json")
    h = headers(request)
    if bad == "key":
        h["Idempotency-Key"] = str(uuid4())
    if bad == "new_patient":
        body["patient"] = {
            "kind": "new",
            "name": "Invented patient",
            "provisional": True,
        }
    if bad == "cookie":
        h["Cookie"] = "synthetic=1"
    if bad == "version":
        h["X-Cosmaneura-Scribe-Encounter-Version"] = "3"
    if bad == "private_note":
        body["action"] = "private_note_save"
    if bad == "missing_workload":
        del app.dependency_overrides[route.authenticate_native_workload]
        del h["X-Cosmaneura-Workload-Authorization"]
    if bad == "missing_human":
        del app.dependency_overrides[route.authenticate_native_workload]
        del h["Authorization"]
    result = http.post(route.PATH, json=body, headers=h)
    assert result.status_code in {401, 403, 422, 426} and calls == []


def test_patient_search_uses_same_existing_provider_owned_reader(monkeypatch):
    request = PatientsRequest(
        action="patients",
        request_nonce=uuid4(),
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        query="Invented",
    )
    monkeypatch.setattr(
        service.scribe_provider_context, "_connection", lambda **_: connection()
    )
    calls = []

    def writer(**kw):
        calls.append(kw["request"])
        return response(kw["request"])

    monkeypatch.setattr(scribe_encounters, "encounter_workflow", writer)
    assert invoke(request)["patients"] == [] and calls == [request]
