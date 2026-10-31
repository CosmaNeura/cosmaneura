"""Offline full native facade; existing writers retain authority and receipts."""

import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.configuration import scribe_native
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_native_encounters as route
from app.services import authority, scribe_encounters, scribe_provider_context
from app.services import scribe_native_encounters as service
from app.services.scribe_encounter_contract import REQUEST_ADAPTER, CreateRequest

from tests.authority_kernel import scribe_native_encounters_test as existing

APPOINTMENT = "appointment_00000000000000000000000007"


def full_gate(monkeypatch, enabled=True):
    monkeypatch.setattr(
        scribe_native, "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED", enabled
    )
    monkeypatch.setattr(
        scribe_native, "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED", True
    )
    monkeypatch.setattr(scribe_native, "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED", True)
    monkeypatch.setattr(
        scribe_native,
        "SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS",
        SimpleNamespace(enabled=True),
    )


def request(action):
    if action == "create":
        return existing.creation(
            patient=dict(
                kind="new",
                name="Invented walk-in",
                date_of_birth=None,
                provisional=True,
            )
        )
    values = dict(
        action=action,
        expected_actor_ref=existing.ACTOR,
        expected_tenant_ref=existing.TENANT,
        request_nonce=str(uuid4()),
        appointment_ref=APPOINTMENT,
    )
    if action == "patient_match_candidates":
        values.update(query="Invented walk-in", date_of_birth="1990-01-02")
    if action == "patient_match_save":
        values.update(
            selection=dict(kind="confirm_new", date_of_birth="1990-01-02"),
            expected_version=0,
            change_key=str(uuid4()),
        )
    if action == "private_note_save":
        values.update(
            text="Invented author reminder", expected_version=0, change_key=str(uuid4())
        )
    return REQUEST_ADAPTER.validate_python(values)


ACTIONS = [
    "create",
    "patient_match_candidates",
    "patient_match_save",
    "private_note_read",
    "private_note_save",
]


def invoke(data, *, full=True, settings=None):
    return service.execute_native_encounter(
        issuer=existing.ISSUER,
        subject="user_synthetic",
        session_id="sess_synthetic",
        request=data,
        authority_settings=existing.AUTH,
        settings_provider=settings or (lambda: ScribeEncounterSettings(True)),
        deadline=time.monotonic() + 10,
        native_visit_actions_v2=full,
    )


def domain_response(data):
    return dict(
        version="1",
        action=data.action,
        request_nonce=str(data.request_nonce),
        account=dict(actor_ref=existing.ACTOR, tenant_ref=existing.TENANT),
    )


@pytest.mark.parametrize("action", ACTIONS)
def test_full_actions_reuse_exact_existing_writer_and_current_scope(
    monkeypatch, action
):
    full_gate(monkeypatch)
    data = request(action)
    calls = []

    def current(**kwargs):
        assert kwargs["session_id"] == "sess_synthetic"
        calls.append("current")
        return existing.connection()

    def writer(**kwargs):
        assert kwargs["request"] is data
        assert kwargs["authority_settings"] is existing.AUTH
        assert kwargs["settings_provider"]().enabled
        calls.append("writer")
        return domain_response(data)

    monkeypatch.setattr(scribe_provider_context, "_connection", current)
    monkeypatch.setattr(scribe_encounters, "encounter_workflow", writer)
    assert invoke(data) == {**domain_response(data), "version": "2"}
    assert calls == ["current", "writer", "current"]


@pytest.mark.parametrize("action", ACTIONS)
def test_v1_still_denies_full_actions_when_full_gate_is_enabled(monkeypatch, action):
    full_gate(monkeypatch)
    monkeypatch.setattr(
        scribe_provider_context,
        "_connection",
        lambda **_: pytest.fail("v1 must deny before current scope or writer"),
    )
    with pytest.raises(ValueError):
        invoke(request(action), full=False)


@pytest.mark.parametrize("changed", ["disabled", "foreign_actor", "foreign_tenant"])
def test_full_mode_cannot_bypass_gate_or_current_authority(monkeypatch, changed):
    full_gate(monkeypatch, enabled=changed != "disabled")
    data = request("create")
    if changed != "disabled":
        field = (
            "expected_actor_ref"
            if changed == "foreign_actor"
            else "expected_tenant_ref"
        )
        data = data.model_copy(
            update={field: getattr(existing.creation(), field)[:-1] + "9"}
        )
    monkeypatch.setattr(
        scribe_provider_context, "_connection", lambda **_: existing.connection()
    )
    monkeypatch.setattr(
        scribe_encounters,
        "encounter_workflow",
        lambda **_: pytest.fail("Denied full operation reached writer"),
    )
    with pytest.raises(
        (service.RecordUnavailable, authority.AuthorityRequestDeniedError)
    ):
        invoke(data)


def test_full_gate_is_rechecked_inside_existing_commit_settings(monkeypatch):
    full_gate(monkeypatch)
    monkeypatch.setattr(
        scribe_provider_context, "_connection", lambda **_: existing.connection()
    )
    monkeypatch.setattr(
        scribe_native, "SCRIBE_ENCOUNTER_SETTINGS", ScribeEncounterSettings(True)
    )

    def writer(**kwargs):
        gate = kwargs["settings_provider"]
        assert gate().enabled
        monkeypatch.setattr(
            scribe_native, "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED", False
        )
        assert not gate().enabled
        raise service.RecordUnavailable

    monkeypatch.setattr(scribe_encounters, "encounter_workflow", writer)
    with pytest.raises(service.RecordUnavailable):
        invoke(request("private_note_save"))


def test_full_response_denies_changed_context_after_writer(monkeypatch):
    full_gate(monkeypatch)
    reads = iter(
        [
            existing.connection(),
            existing.connection().model_copy(update={"review_sha256": "b" * 64}),
        ]
    )
    monkeypatch.setattr(scribe_provider_context, "_connection", lambda **_: next(reads))
    monkeypatch.setattr(
        scribe_encounters,
        "encounter_workflow",
        lambda **kw: domain_response(kw["request"]),
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request("patient_match_save"))


def full_client(monkeypatch):
    http, _, app = existing.client(monkeypatch)
    full_gate(monkeypatch)
    monkeypatch.setattr(
        route, "execute_native_encounter", service.execute_native_encounter
    )
    monkeypatch.setattr(
        scribe_provider_context, "_connection", lambda **_: existing.connection()
    )
    calls = []

    def writer(**kwargs):
        calls.append(kwargs)
        return domain_response(kwargs["request"])

    monkeypatch.setattr(scribe_encounters, "encounter_workflow", writer)
    return http, calls, app


def headers(data, version="2"):
    key = (
        data.creation_key
        if isinstance(data, CreateRequest)
        else getattr(data, "change_key", uuid4())
    )
    return {
        "Authorization": "Bearer synthetic-human",
        "X-Cosmaneura-Workload-Authorization": "Bearer synthetic-workload",
        "X-Cosmaneura-Scribe-Encounter-Version": version,
        "Idempotency-Key": str(key),
    }


@pytest.mark.parametrize("action", ACTIONS)
def test_v2_route_dispatches_five_actions_with_version_and_original_key(
    monkeypatch, action
):
    http, calls, _ = full_client(monkeypatch)
    data = request(action)
    result = http.post(
        route.PATH, json=data.model_dump(mode="json"), headers=headers(data)
    )
    assert result.status_code == 200
    assert result.headers["x-cosmaneura-scribe-encounter-version"] == "2"
    assert (
        result.headers["cache-control"] == "no-store"
        and result.json()["version"] == "2"
    )
    assert len(calls) == 1 and calls[0]["request"] == data


@pytest.mark.parametrize("action", ACTIONS)
def test_full_route_never_widens_v1(monkeypatch, action):
    http, calls, _ = full_client(monkeypatch)
    data = request(action)
    result = http.post(
        route.PATH, json=data.model_dump(mode="json"), headers=headers(data, "1")
    )
    assert result.status_code == 422 and calls == []


@pytest.mark.parametrize(
    "bad", ["disabled", "key", "cookie", "extra", "unknown_version"]
)
def test_full_route_fails_before_writer_for_invalid_mode_or_binding(monkeypatch, bad):
    http, calls, _ = full_client(monkeypatch)
    data = request("private_note_save")
    body, h = data.model_dump(mode="json"), headers(data)
    if bad == "disabled":
        full_gate(monkeypatch, enabled=False)
    elif bad == "key":
        h["Idempotency-Key"] = str(uuid4())
    elif bad == "cookie":
        h["Cookie"] = "synthetic=1"
    elif bad == "extra":
        body["creator_actor_id"] = existing.ACTOR
    else:
        h["X-Cosmaneura-Scribe-Encounter-Version"] = "3"
    result = http.post(route.PATH, json=body, headers=h)
    assert result.status_code in {403, 422, 426, 503} and calls == []


@pytest.mark.parametrize("version", ["1", "2"])
def test_existing_patient_stays_compatible_in_each_explicit_mode(monkeypatch, version):
    http, calls, _ = full_client(monkeypatch)
    data = existing.creation()
    result = http.post(
        route.PATH, json=data.model_dump(mode="json"), headers=headers(data, version)
    )
    assert result.status_code == 200 and result.json()["version"] == version
    assert calls[0]["request"] == data


@pytest.mark.parametrize(
    "action", ["create", "patient_match_save", "private_note_save"]
)
def test_v2_mutation_body_key_must_equal_header(monkeypatch, action):
    http, calls, _ = full_client(monkeypatch)
    data = request(action)
    h = headers(data)
    h["Idempotency-Key"] = str(uuid4())
    result = http.post(route.PATH, json=data.model_dump(mode="json"), headers=h)
    assert result.status_code == 422 and calls == []


@pytest.mark.parametrize("missing", ["human", "workload"])
def test_full_mode_cannot_skip_either_authentication_boundary(monkeypatch, missing):
    http, calls, app = full_client(monkeypatch)
    data = request("create")
    h = headers(data)
    del app.dependency_overrides[route.authenticate_native_workload]
    if missing == "human":
        del h["Authorization"]
    else:
        del h["X-Cosmaneura-Workload-Authorization"]
    result = http.post(route.PATH, json=data.model_dump(mode="json"), headers=h)
    assert result.status_code in {401, 403} and calls == []


@pytest.mark.parametrize("action", ["list", "lookup", "add_birth_date"])
def test_v2_does_not_expand_to_other_existing_encounter_actions(monkeypatch, action):
    http, calls, _ = full_client(monkeypatch)
    body = dict(
        action=action,
        expected_actor_ref=existing.ACTOR,
        expected_tenant_ref=existing.TENANT,
        request_nonce=str(uuid4()),
    )
    if action == "lookup":
        body["workspace_id"] = str(uuid4())
    if action == "add_birth_date":
        body.update(
            appointment_ref=APPOINTMENT,
            patient_ref=existing.PATIENT,
            date_of_birth="1990-01-02",
            change_key=str(uuid4()),
        )
    data = REQUEST_ADAPTER.validate_python(body)
    with pytest.raises(ValueError):
        invoke(data)
    result = http.post(route.PATH, json=body, headers=headers(data))
    assert result.status_code == 422 and calls == []


@pytest.mark.parametrize(
    "prerequisite",
    [
        "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED",
        "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED",
        "workload",
    ],
)
def test_full_gate_cannot_replace_existing_native_prerequisites(
    monkeypatch, prerequisite
):
    full_gate(monkeypatch)
    if prerequisite == "workload":
        monkeypatch.setattr(
            scribe_native,
            "SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS",
            SimpleNamespace(enabled=False),
        )
    else:
        monkeypatch.setattr(scribe_native, prerequisite, False)
    assert not scribe_native.native_visit_actions_v2_enabled()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("opted", [False, True])
def test_context_capability_is_explicit_and_old_reply_shape_is_preserved(
    monkeypatch, enabled, opted
):
    full_gate(monkeypatch, enabled=enabled)
    monkeypatch.setattr(
        scribe_provider_context, "_connection", lambda **_: existing.connection()
    )
    values = dict(action="connection", request_nonce=uuid4())
    if opted:
        values["include_native_visit_actions_v2"] = True
    result = scribe_provider_context.read_provider_context(
        issuer=existing.ISSUER,
        subject="user_synthetic",
        session_id="sess_synthetic",
        request=scribe_provider_context.ProviderContextRequest(**values),
        authority_settings=existing.AUTH,
        deadline=time.monotonic() + 10,
    )
    wire = json.loads(result.model_dump_json())
    expected = {
        "contract_version",
        "action",
        "request_nonce",
        "checked_at",
        "connection",
        "catalog",
    }
    assert set(wire) == expected | ({"native_visit_actions_v2"} if opted else set())
    if opted:
        assert wire["native_visit_actions_v2"] is enabled


@pytest.mark.parametrize("value", [False, 0, 1, "true", None])
def test_context_capability_opt_in_is_strict_true(value):
    with pytest.raises(ValidationError):
        scribe_provider_context.ProviderContextRequest(
            action="connection",
            request_nonce=uuid4(),
            include_native_visit_actions_v2=value,
        )


def test_provisional_retry_stays_bound_to_existing_creation_digest():
    data = request("create")
    digest = existing.digest(data.model_dump(mode="json", exclude={"request_nonce"}))
    row = SimpleNamespace(request_sha256=digest)
    session = SimpleNamespace(exec=lambda _: SimpleNamespace(one_or_none=lambda: row))
    assert scribe_encounters._create(
        session, data, "doctor_synthetic", None, existing.ISSUER, "user_synthetic"
    ) == (row, False)
    retried = data.model_copy(update={"request_nonce": uuid4()})
    assert scribe_encounters._create(
        session, retried, "doctor_synthetic", None, existing.ISSUER, "user_synthetic"
    ) == (row, False)
    with pytest.raises(scribe_encounters.EncounterConflict):
        scribe_encounters._create(
            session,
            data.model_copy(update={"title": "Different visit"}),
            "doctor_synthetic",
            None,
            existing.ISSUER,
            "user_synthetic",
        )
