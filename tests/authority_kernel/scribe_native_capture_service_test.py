"""Fresh boundaries in the composed path, including real owned PostgreSQL."""

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_native_capture as route
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.embed_context import VerifiedClerkIdentity
from app.services import authority, provider_context_exchange
from app.services import scribe_capture_admission as admission
from app.services import scribe_native_capture as service
from app.services.scribe_capture_deadline import CaptureBudget, capture_budget
from app.services.scribe_capture_protocol import ProviderEncounter, ReadOperation
from app.services.scribe_workflow import DisabledScribeWorkflowClient

from activemodel import session_manager
from app.models.doctor import Doctor
from app.models.practice import Practice
from app.models.scribe_capture_binding import ScribeCaptureBinding
from app.models.superadmin_capability import SuperadminCapabilityEnvironment
from sqlmodel import Session, select

from tests.authority_kernel.companion_provider_provisioning_test import (
    SETTINGS,
    session_for,
)
from tests.authority_kernel.scribe_encounters_test import engine as _engine
from tests.authority_kernel.scribe_encounters_test import workflow as _workflow

engine = _engine
workflow = _workflow

ACTOR = "authority_actor_00000000000000000000000001"
TENANT = "authority_tenant_00000000000000000000000002"
BINDING = "scribe_capture_binding_00000000000000000000000003"


def fixture(monkeypatch):
    monkeypatch.setattr(service, "global_session", nullcontext)
    data = service.NativeCaptureRequest(
        action="mount",
        workspace_id=uuid4(),
        request_nonce=uuid4(),
        opening_nonce=uuid4(),
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
    )
    calls = []
    state = {"revoked": False}
    row = {
        "workspace_id": str(data.workspace_id),
        "appointment_ref": "appointment_00000000000000000000000004",
        "patient_ref": "patient_00000000000000000000000005",
    }

    def lookup(**kwargs):
        calls.append("lookup")
        assert kwargs["session_id"] == "session_test"
        if kwargs.get("expected_capture_binding_ref") not in (None, BINDING):
            raise authority.AuthorityRequestDeniedError
        if state["revoked"]:
            raise authority.AuthorityRequestDeniedError
        return {"encounter": row.copy()}

    def exchange(**kwargs):
        calls.append("exchange")
        return SimpleNamespace(context_token="never-return-context-token")

    def resolve(**kwargs):
        calls.append("resolve")
        return SimpleNamespace(
            context=SimpleNamespace(
                surface="provider_assistant",
                auth_tier="provider",
                actor_id=ACTOR,
                tenant_id=TENANT,
            ),
            interaction_ref="never-return-interaction",
        )

    def bind(**kwargs):
        calls.append("binding")
        return SimpleNamespace(binding_ref=BINDING)

    async def execute(**kwargs):
        calls.append(("operation", kwargs))
        return ProviderEncounter(status="not_prepared", review=None)

    monkeypatch.setattr(service.scribe_encounters, "encounter_workflow", lookup)
    monkeypatch.setattr(service, "provider_exchange_blocked", lambda **_: False)
    monkeypatch.setattr(authority, "exchange_context_token", exchange)
    monkeypatch.setattr(authority, "resolve_mount_context", resolve)
    monkeypatch.setattr(service.scribe_capture_scope, "resolve_capture_binding", bind)
    monkeypatch.setattr(service, "execute_capture_operation", execute)

    async def invoke(value=data):
        with capture_budget(CaptureBudget(10)):
            return await service.execute_native_capture(
                issuer="https://test.clerk.invalid",
                subject="user_test",
                clerk_session_id="session_test",
                workload_client_id="workload_test",
                data=value,
                key="original-intent-key",
                authority_settings=SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
                encounter_settings=ScribeEncounterSettings(enabled=True),
                client=DisabledScribeWorkflowClient(),
            )

    return data, calls, state, invoke


@pytest.mark.asyncio
async def test_mount_composes_existing_services_and_keeps_original_intent(monkeypatch):
    data, calls, _, invoke = fixture(monkeypatch)
    result = await invoke()
    assert isinstance(result["result"], dict)
    assert [c if isinstance(c, str) else c[0] for c in calls] == [
        "lookup",
        "exchange",
        "lookup",
        "resolve",
        "lookup",
        "binding",
        "lookup",
        "operation",
        "lookup",
    ]
    operation = calls[-2][1]
    assert operation["client_request_key"] == "original-intent-key"
    assert operation["workspace_id"] == data.workspace_id
    assert operation["caller"].mount_context_token == "never-return-context-token"
    assert result["result"]["opening_review"]["request_nonce"] == str(
        data.opening_nonce
    )
    assert "never-return" not in str(result)


@pytest.mark.asyncio
async def test_uncertain_admission_opens_only_bound_recovery_after_final_access_check(
    monkeypatch,
):
    data, calls, _, invoke = fixture(monkeypatch)

    async def uncertain(**kwargs):
        calls.append(("operation", kwargs))
        assert kwargs["operation"].kind == "provider_encounter"
        raise admission.AdmissionUnconfirmed

    monkeypatch.setattr(service, "execute_capture_operation", uncertain)
    result = (await invoke())["result"]
    assert isinstance(result, dict)
    assert "opening_review" not in result
    assert result["opening_recovery"] == {
        "contract_version": "1",
        "workspace_id": str(data.workspace_id),
        "binding_ref": BINDING,
        "request_nonce": str(data.opening_nonce),
        "disposition": "provider_operation_conflict",
    }
    assert calls[-1] == "lookup"
    assert sum(isinstance(call, tuple) for call in calls) == 1
    assert "never-return" not in str(result)


@pytest.mark.asyncio
async def test_uncertain_opening_cannot_publish_after_access_revocation(monkeypatch):
    _, _, state, invoke = fixture(monkeypatch)

    async def uncertain(**kwargs):
        state["revoked"] = True
        raise admission.AdmissionUnconfirmed

    monkeypatch.setattr(service, "execute_capture_operation", uncertain)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["provider_encounter", "native_recording_preparation"])
async def test_uncertain_ordinary_read_never_becomes_opening_recovery(
    monkeypatch, kind
):
    data, _, _, invoke = fixture(monkeypatch)

    async def uncertain(**kwargs):
        raise admission.AdmissionUnconfirmed

    monkeypatch.setattr(service, "execute_capture_operation", uncertain)
    operation = service.NativeCaptureRequest(
        action="operation",
        workspace_id=data.workspace_id,
        binding_ref=BINDING,
        request_nonce=uuid4(),
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        operation=ReadOperation(kind=kind),
    )
    with pytest.raises(admission.AdmissionUnconfirmed):
        await invoke(operation)


@pytest.mark.asyncio
async def test_current_clerk_session_view_as_guard_prevents_context_issuance(
    monkeypatch,
):
    _, calls, _, invoke = fixture(monkeypatch)

    def blocked(**values):
        assert values["clerk_session_id"] == "session_test"
        assert values["issuer"] == "https://test.clerk.invalid"
        assert values["subject"] == "user_test"
        return True

    monkeypatch.setattr(service, "provider_exchange_blocked", blocked)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke()
    assert calls == ["lookup"]


@pytest.mark.parametrize("blocked", [False, True])
def test_shared_exchange_guard_preserves_owner_lock_before_view_as(
    monkeypatch, blocked
):
    transaction = object()
    calls = []
    monkeypatch.setattr(
        provider_context_exchange, "get_session", lambda: nullcontext(transaction)
    )
    monkeypatch.setattr(
        provider_context_exchange,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    monkeypatch.setattr(
        provider_context_exchange.session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SimpleNamespace(allows=lambda subject: subject == "user_test"),
    )

    def lock(**values):
        assert values == {"session": transaction, "clerk_subject": "user_test"}
        calls.append("owner_lock")

    def guard(session, **values):
        assert session is transaction
        assert values == {
            "issuer": "https://test.clerk.invalid",
            "subject": "user_test",
            "clerk_session_id": "session_test",
            "environment": SuperadminCapabilityEnvironment.staging,
        }
        calls.append("view_as")
        return blocked

    monkeypatch.setattr(provider_context_exchange, "lock_provider_record_owner", lock)
    monkeypatch.setattr(
        provider_context_exchange, "context_token_exchange_blocked_by_view_as", guard
    )
    assert (
        provider_context_exchange.provider_exchange_blocked(
            issuer="https://test.clerk.invalid",
            subject="user_test",
            clerk_session_id="session_test",
            requested_surface="provider_assistant",
            record_ref="appointment_invented",
            settings=SETTINGS,
        )
        is blocked
    )
    assert calls == ["owner_lock", "view_as"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["exchange", "resolve", "binding", "operation"])
async def test_revocation_after_each_service_boundary_prevents_publication(
    monkeypatch, phase
):
    _, calls, state, invoke = fixture(monkeypatch)
    owner, name = {
        "exchange": (authority, "exchange_context_token"),
        "resolve": (authority, "resolve_mount_context"),
        "binding": (service.scribe_capture_scope, "resolve_capture_binding"),
        "operation": (service, "execute_capture_operation"),
    }[phase]
    original = getattr(owner, name)
    if phase == "operation":

        async def changed_async(**kwargs):
            result = await original(**kwargs)
            state["revoked"] = True
            return result

        changed = changed_async
    else:

        def changed(**kwargs):
            result = original(**kwargs)
            state["revoked"] = True
            return result

    monkeypatch.setattr(owner, name, changed)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke()
    assert sum(isinstance(c, tuple) for c in calls) == (
        1 if phase == "operation" else 0
    )


@pytest.mark.asyncio
async def test_cancellation_during_remote_operation_has_no_final_lookup(monkeypatch):
    _, calls, _, invoke = fixture(monkeypatch)
    entered = asyncio.Event()

    async def held(**kwargs):
        entered.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(service, "execute_capture_operation", held)
    pending = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        before = list(calls)
    finally:
        pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert calls == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["actor_id", "tenant_id", "surface", "auth_tier"])
async def test_resolved_authority_mismatch_never_reaches_binding_or_operation(
    monkeypatch, field
):
    _, calls, _, invoke = fixture(monkeypatch)
    original = authority.resolve_mount_context

    def altered(**kwargs):
        result = original(**kwargs)
        setattr(result.context, field, "wrong")
        return result

    monkeypatch.setattr(authority, "resolve_mount_context", altered)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke()
    assert "binding" not in calls and not any(isinstance(c, tuple) for c in calls)


@pytest.mark.asyncio
async def test_real_core_created_mount_preserves_binding_and_current_record_checks(
    engine, workflow, monkeypatch
):
    monkeypatch.setattr(service, "global_session", lambda: session_for(engine))
    args, _, request, invoke = workflow
    created = invoke(request())
    row, account = created["encounter"], created["account"]
    for owner in (authority, service.scribe_capture_scope, provider_context_exchange):
        monkeypatch.setattr(owner, "get_session", lambda: session_for(engine))
    monkeypatch.setattr(admission, "_database", lambda: session_for(engine))
    monkeypatch.setattr(
        provider_context_exchange,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    data = service.NativeCaptureRequest(
        action="mount",
        workspace_id=row["workspace_id"],
        request_nonce=uuid4(),
        opening_nonce=uuid4(),
        expected_actor_ref=account["actor_ref"],
        expected_tenant_ref=account["tenant_ref"],
    )

    async def call(value=data):
        with capture_budget(CaptureBudget(15)):
            return await service.execute_native_capture(
                issuer=SETTINGS.clerk_issuer,
                subject=args["verified_clerk_subject"],
                clerk_session_id="session_invented_scribe",
                workload_client_id="11111111-1111-4111-8111-111111111111",
                data=value,
                key="original-native-mount",
                authority_settings=SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
                encounter_settings=ScribeEncounterSettings(enabled=True),
                client=DisabledScribeWorkflowClient(),
            )

    result = await call()
    assert isinstance(result["result"], dict)
    assert result["result"]["opening_review"]["receipt"]["status"] == "not_prepared"
    assert result["result"]["appointment_ref"] == row["appointment_ref"]
    repeated = await call(data.model_copy(update={"request_nonce": uuid4()}))
    assert isinstance(repeated["result"], dict)
    assert repeated["result"]["binding_ref"] == result["result"]["binding_ref"]
    with session_for(engine) as session:
        bindings = list(
            session.exec(
                select(ScribeCaptureBinding).where(
                    ScribeCaptureBinding.workspace_id == data.workspace_id
                )
            )
        )
        assert len(bindings) == 1
        bindings[0].revoked_at = datetime.now(UTC)
        session.add(bindings[0])
        session.commit()
    with pytest.raises(authority.AuthorityKernelError):
        await call()


def test_actual_router_closes_phase_transactions_before_read_and_sees_revocation(
    engine, workflow, monkeypatch
):
    args, _, request, invoke = workflow
    created = invoke(request())
    row, account = created["encounter"], created["account"]
    # Restore production transaction handling after the fixture's setup. No
    # service session factory or request-wide dependency is replaced in this run.
    monkeypatch.setattr(
        service.scribe_encounters, "global_session", session_manager.global_session
    )
    monkeypatch.setattr(
        session_manager.SessionManager.get_instance(), "_engine", engine
    )
    monkeypatch.setattr(
        provider_context_exchange,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )
    app = FastAPI()
    app.include_router(route.native_capture_api_app)
    app.dependency_overrides[route.authenticate_native_workload] = (
        lambda: EntraServicePrincipal("11111111-1111-4111-8111-111111111111")
    )
    app.dependency_overrides[route.authenticate_embed_platform_session] = (
        lambda: VerifiedClerkIdentity(
            SETTINGS.clerk_issuer,
            args["verified_clerk_subject"],
            "session_invented_scribe",
        )
    )
    app.dependency_overrides[route.get_capture_authority_settings] = lambda: SETTINGS
    app.dependency_overrides[route.get_capture_settings] = (
        lambda: ScribeCaptureSettings(enabled=True)
    )
    app.dependency_overrides[route.encounter_settings] = (
        lambda: ScribeEncounterSettings(enabled=True)
    )
    app.dependency_overrides[route.get_admission_client_factory] = (
        lambda: DisabledScribeWorkflowClient
    )
    observed = []
    original = service.execute_capture_operation

    async def read_then_revoke(**kwargs):
        assert session_manager._session_context.get() is None
        assert engine.pool.checkedout() == 0
        result = await original(**kwargs)
        assert isinstance(result, ProviderEncounter)
        assert result.status == "not_prepared"
        assert session_manager._session_context.get() is None
        assert engine.pool.checkedout() == 0
        # A different committed transaction acquires every relevant owner lock
        # without waiting while the route is still active at its async boundary.
        with Session(engine) as session:
            for model, identity in (
                (Practice, args["practice_id"]),
                (Doctor, args["doctor_id"]),
            ):
                assert session.exec(
                    select(model)
                    .where(model.id == identity)
                    .with_for_update(nowait=True)
                ).one()
            binding = session.exec(
                select(ScribeCaptureBinding)
                .where(ScribeCaptureBinding.workspace_id == kwargs["workspace_id"])
                .with_for_update(nowait=True)
            ).one()
            binding.revoked_at = datetime.now(UTC)
            session.add(binding)
            session.commit()
            observed.append("revocation_committed_without_held_locks")
        await asyncio.sleep(0)
        return result

    monkeypatch.setattr(service, "execute_capture_operation", read_then_revoke)
    with TestClient(app) as connection:
        result = connection.post(
            "/service/v1/agent" + route.PATH,
            json={
                "action": "mount",
                "workspace_id": row["workspace_id"],
                "request_nonce": str(uuid4()),
                "opening_nonce": str(uuid4()),
                "expected_actor_ref": account["actor_ref"],
                "expected_tenant_ref": account["tenant_ref"],
            },
            headers={
                "Content-Type": "application/json",
                "X-Cosmaneura-Native-Capture-Version": "1",
                "Idempotency-Key": "retained-native-mount",
            },
        )
    assert result.status_code == 403
    assert observed == ["revocation_committed_without_held_locks"]
    assert "opening_review" not in result.text
    assert engine.pool.checkedout() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["admission", "recording_window"])
async def test_preparation_context_reuses_services_without_capture_mutation(
    monkeypatch, kind
):
    original, calls, _, invoke = fixture(monkeypatch)
    data = service.NativeCaptureRequest(
        action="preparation_context",
        preparation=kind,
        workspace_id=original.workspace_id,
        request_nonce=original.request_nonce,
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        binding_ref=BINDING,
    )
    value = await invoke(data)
    assert not any(isinstance(c, tuple) for c in calls)
    assert calls == [
        "lookup",
        "exchange",
        "lookup",
        "resolve",
        "lookup",
    ]
    result = value["result"]
    assert isinstance(result, dict)
    assert (
        result["context_token"] == "never-return-context-token"
        and result["interaction_ref"] == "never-return-interaction"
    )
    assert result["preparation"] == kind and result["binding_ref"] == BINDING


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["admission", "recording_window"])
@pytest.mark.parametrize("change", ["workspace_id", "appointment_ref", "patient_ref"])
async def test_preparation_publication_checks_current_record_after_last_service(
    monkeypatch, kind, change
):
    original, calls, _, invoke = fixture(monkeypatch)
    lookup = service.scribe_encounters.encounter_workflow
    boundary = "resolve"

    def reassigned(**kwargs):
        value = lookup(**kwargs)
        if boundary in calls:
            value["encounter"][change] = "different-current-record"
        return value

    monkeypatch.setattr(service.scribe_encounters, "encounter_workflow", reassigned)
    data = service.NativeCaptureRequest(
        action="preparation_context",
        preparation=kind,
        workspace_id=original.workspace_id,
        request_nonce=original.request_nonce,
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        binding_ref=BINDING,
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke(data)
    assert not any(isinstance(c, tuple) for c in calls)


@pytest.mark.asyncio
async def test_preparation_context_binding_mismatch_never_returns_credentials(
    monkeypatch,
):
    original, calls, _, invoke = fixture(monkeypatch)
    data = service.NativeCaptureRequest(
        action="preparation_context",
        preparation="admission",
        workspace_id=original.workspace_id,
        request_nonce=original.request_nonce,
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        binding_ref="scribe_capture_binding_00000000000000000000000009",
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke(data)
    assert not any(isinstance(c, tuple) for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "phase"),
    [
        ("admission", "exchange"),
        ("admission", "resolve"),
        ("recording_window", "exchange"),
        ("recording_window", "resolve"),
    ],
)
async def test_preparation_context_revocation_at_every_original_boundary(
    monkeypatch, kind, phase
):
    original, calls, state, invoke = fixture(monkeypatch)
    data = service.NativeCaptureRequest(
        action="preparation_context",
        preparation=kind,
        workspace_id=original.workspace_id,
        request_nonce=original.request_nonce,
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
        binding_ref=BINDING,
    )
    owner, name = {
        "exchange": (authority, "exchange_context_token"),
        "resolve": (authority, "resolve_mount_context"),
        "binding": (service.scribe_capture_scope, "resolve_capture_binding"),
    }[phase]
    original_call = getattr(owner, name)

    def changed(**kw):
        value = original_call(**kw)
        state["revoked"] = True
        return value

    monkeypatch.setattr(owner, name, changed)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke(data)
    assert not any(isinstance(c, tuple) for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["admission", "recording_window"])
async def test_real_preparation_context_commits_and_closes_all_policy_transactions(
    engine, workflow, monkeypatch, kind
):
    args, _, request, invoke = workflow
    created = invoke(request())
    row, account = created["encounter"], created["account"]
    monkeypatch.setattr(
        service.scribe_encounters, "global_session", session_manager.global_session
    )
    monkeypatch.setattr(
        session_manager.SessionManager.get_instance(), "_engine", engine
    )
    monkeypatch.setattr(
        provider_context_exchange,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )

    async def call(data):
        with capture_budget(CaptureBudget(20)):
            return await service.execute_native_capture(
                issuer=SETTINGS.clerk_issuer,
                subject=args["verified_clerk_subject"],
                clerk_session_id="session_invented_scribe",
                workload_client_id="11111111-1111-4111-8111-111111111111",
                data=data,
                key="original-preparation-key",
                authority_settings=SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
                encounter_settings=ScribeEncounterSettings(enabled=True),
                client=DisabledScribeWorkflowClient(),
            )

    mounted = await call(
        service.NativeCaptureRequest(
            action="mount",
            workspace_id=row["workspace_id"],
            request_nonce=uuid4(),
            expected_actor_ref=account["actor_ref"],
            expected_tenant_ref=account["tenant_ref"],
        )
    )
    mounted_result = mounted["result"]
    assert isinstance(mounted_result, dict)
    data = service.NativeCaptureRequest(
        action="preparation_context",
        preparation=kind,
        workspace_id=row["workspace_id"],
        request_nonce=uuid4(),
        binding_ref=mounted_result["binding_ref"],
        expected_actor_ref=account["actor_ref"],
        expected_tenant_ref=account["tenant_ref"],
    )
    prepared = await call(data)
    prepared_result = prepared["result"]
    assert isinstance(prepared_result, dict)
    assert (
        prepared_result["preparation"] == kind
        and session_manager._session_context.get() is None
        and engine.pool.checkedout() == 0
    )
    # Existing resolve route accepts the context issued by the compound service.
    resolved = authority.resolve_mount_context(
        workload_client_id="11111111-1111-4111-8111-111111111111",
        mount_context_token=prepared_result["context_token"],
        client_turn_id=UUID(prepared_result["client_turn_id"]),
        settings=SETTINGS,
    )
    assert resolved.interaction_ref == prepared_result["interaction_ref"]
    with Session(engine) as session:
        binding = session.exec(
            select(ScribeCaptureBinding)
            .where(ScribeCaptureBinding.workspace_id == data.workspace_id)
            .with_for_update(nowait=True)
        ).one()
        binding.revoked_at = datetime.now(UTC)
        session.add(binding)
        session.commit()
    with pytest.raises(authority.AuthorityKernelError):
        await call(data)
    assert engine.pool.checkedout() == 0
