"""Actual prepared native facade/authority/SQL; invented source decisions, no audio."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.services import scribe_native_capture as service
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    DeadlineScribeClient,
    capture_budget,
)
from app.services.scribe_capture_gateway import CaptureOperationUnconfirmed
from app.services.scribe_source_start_contract import (
    NativeSourceStartSequence,
    NativeStartDecision,
    NativeStartPayload,
    NativeStartReceipt,
)
from app.services.scribe_workflow import ScribeWorkflowRetryableError

from app.models.authority import AuthorityActorExternalIdentity, MountContextToken
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from sqlmodel import Session, select

from tests.authority_kernel.companion_provider_provisioning_test import SETTINGS
from tests.authority_kernel.scribe_native_capture_receiver_test import (
    PATH,
    body,
    client,
    headers,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)
from tests.authority_kernel.scribe_prepared_window_context_test import prepared

engine, native, workflow = _engine, _native, _workflow


@pytest.fixture
def source_case(native, engine, monkeypatch):
    context = prepared(native)
    assert native.post("creator_review").status_code == 200
    saved = next(iter(native.remote.sessions.values()))
    at = datetime.now(UTC)
    payload = NativeStartPayload(
        attempt_id=uuid4(),
        source="ios_microphone",
        owner_proof="a" * 64,
        lease_id=uuid4(),
        lease_receipt_id=uuid4(),
        lease_generation=1,
        capture_authorized_until=at + timedelta(minutes=5),
    )
    operation = NativeSourceStartSequence(
        kind="source_start_sequence",
        payload=payload,
        authorize_idempotency_key="invented-original-authorize-key",
    )
    receipt = NativeStartReceipt(
        **payload.model_dump(exclude={"owner_proof"}),
        session_id=saved.id,
        creator_actor_id=saved.creator_actor_id,
        consent_record_id=saved.consent_record_id,
        source_start_id=uuid4(),
        state="prepared",
        preparation_expires_at=at + timedelta(seconds=20),
        issued_at=None,
        expires_at=None,
        capability_id=uuid4(),
    )
    decision = NativeStartDecision(
        receipt=receipt.model_copy(
            update={
                "state": "issued",
                "issued_at": at,
                "expires_at": at + timedelta(seconds=15),
            }
        ),
        secret=payload.owner_proof,
    )
    case = SimpleNamespace(
        context=context,
        operation=operation,
        receipt=receipt,
        decision=decision,
        calls=[],
        hook=None,
    )

    async def remote(tenant, session, value, **kwargs):
        assert engine.pool.checkedout() == 0
        assert tenant == native.remote.tenant and session == saved.id
        assert value.kind == "source_start" and value.payload == payload
        assert kwargs["creator_actor_id"] == saved.creator_actor_id
        case.calls.append((value.action, kwargs["idempotency_key"]))
        result = receipt if value.action == "prepare" else decision
        return case.hook(value.action, result) if case.hook else result

    monkeypatch.setattr(native.remote, "capture_operation", remote)
    return case


def source(native, case, **changes):
    return native.post(
        "creator_review",
        action="prepared_source",
        operation=case.operation.model_dump(mode="json"),
        prepared_context=case.context,
        **changes,
    )


def test_prepared_source_runs_original_decisions_with_exact_receipt(
    native, engine, source_case
):
    response = source(native, source_case)
    assert response.status_code == 200, response.text
    assert response.headers["x-cosmaneura-prepared-source-start-version"] == "1"
    assert response.headers["cache-control"] == "no-store"
    result = response.json()["result"]
    assert result["operation"] == "source_start_sequence"
    assert result["receipt"] == {
        "preparation": source_case.receipt.model_dump(mode="json"),
        "authorization": source_case.decision.model_dump(mode="json"),
    }
    assert source_case.calls == [
        ("prepare", "invented-status-query"),
        ("authorize", "invented-original-authorize-key"),
    ]
    assert source_case.context["context_token"] not in response.text
    assert engine.pool.checkedout() == 0


def mutate(native, engine, context, fault):
    if fault in {"turn", "interaction"}:
        context["client_turn_id" if fault == "turn" else "interaction_ref"] = str(
            uuid4()
        )
        return
    with Session(engine) as session:
        if fault in {"expired", "revoked", "old", "future"}:
            row = session.exec(
                select(MountContextToken).where(
                    MountContextToken.token_hash
                    == hashlib.sha256(context["context_token"].encode()).hexdigest()
                )
            ).one()
            if fault == "expired":
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif fault == "revoked":
                row.revoked_at = datetime.now(UTC)
            else:
                row.issued_at = datetime.now(UTC) + timedelta(
                    seconds=1 if fault == "future" else -31
                )
        elif fault == "identity":
            row = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.subject
                    == native.args["verified_clerk_subject"]
                )
            ).one()
            row.revoked_at = datetime.now(UTC)
        elif fault == "provider_link":
            row = session.get(
                ScribeCaptureProviderLink, native.provisioned.provider_link_ref
            )
            row.revoked_at = datetime.now(UTC)
        elif fault == "binding":
            row = session.exec(
                select(ScribeCaptureBinding).where(
                    ScribeCaptureBinding.workspace_id
                    == UUID(native.row["workspace_id"])
                )
            ).one()
            row.revoked_at = datetime.now(UTC)
        else:
            binding = session.exec(
                select(ScribeCaptureBinding).where(
                    ScribeCaptureBinding.workspace_id
                    == UUID(native.row["workspace_id"])
                )
            ).one()
            row = session.exec(
                select(ScribeCaptureAdmission).where(
                    ScribeCaptureAdmission.binding_id == binding.id
                )
            ).one()
            row.status = "consent_unavailable"
        session.add(row)
        session.commit()


@pytest.mark.parametrize(
    "fault",
    [
        "expired",
        "revoked",
        "old",
        "future",
        "identity",
        "provider_link",
        "binding",
        "consent",
        "turn",
        "interaction",
        "foreign_workspace",
    ],
)
def test_invalid_current_context_scope_denies_before_prepare(
    native, engine, source_case, fault
):
    if fault == "foreign_workspace":
        response = source(native, source_case, workspace_id=str(uuid4()))
    else:
        mutate(native, engine, source_case.context, fault)
        response = source(native, source_case)
    assert response.status_code in {403, 409}, response.text
    assert source_case.calls == [] and "receipt" not in response.json()
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("phase", ["prepare", "authorize"])
@pytest.mark.parametrize(
    "fault",
    [
        "revoked",
        "provider_link",
        "old",
        "consent",
        "reply_loss",
        "wrong_session",
        "wrong_lease",
        "wrong_proof",
        "malformed",
    ],
)
def test_remote_changes_or_uncertainty_withhold_next_decision_and_result(
    native, engine, source_case, phase, fault
):
    def altered(action, result):
        if action != phase:
            return result
        if fault in {"revoked", "provider_link", "old", "consent"}:
            mutate(native, engine, source_case.context, fault)
        elif fault == "reply_loss":
            raise ScribeWorkflowRetryableError()
        elif fault == "malformed":
            return (
                result.model_copy(update={"receipt": {}})
                if action == "authorize"
                else result.model_copy(update={"state": "cancelled"})
            )
        elif fault == "wrong_proof":
            if action == "authorize":
                return result.model_copy(update={"secret": "b" * 64})
            return result.model_copy(update={"attempt_id": uuid4()})
        else:
            key = "session_id" if fault == "wrong_session" else "lease_id"
            if action == "authorize":
                return result.model_copy(
                    update={"receipt": result.receipt.model_copy(update={key: uuid4()})}
                )
            return result.model_copy(update={key: uuid4()})
        return result

    source_case.hook = altered
    response = source(native, source_case)
    assert response.status_code in {403, 409, 503}, response.text
    assert len(source_case.calls) == (1 if phase == "prepare" else 2)
    assert (
        "receipt" not in response.json()
        and source_case.context["context_token"] not in response.text
    )
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("phase", ["prepare", "authorize"])
def test_capture_cancellation_cannot_return_source_permission(
    native, engine, source_case, phase
):
    budget = CaptureBudget(30)

    def cancel(action, result):
        if action == phase:
            budget.cancel()
        return result

    source_case.hook = cancel
    # Use the actual admitted binding and mount identity from the fixture request.
    with Session(engine) as session:
        binding = session.exec(
            select(ScribeCaptureBinding).where(
                ScribeCaptureBinding.workspace_id == UUID(native.row["workspace_id"])
            )
        ).one()
        ref = str(binding.id)
    request = service.NativeCaptureRequest.model_validate(
        body(
            action="prepared_source",
            workspace_id=native.row["workspace_id"],
            binding_ref=ref,
            expected_actor_ref=native.request().expected_actor_ref,
            expected_tenant_ref=native.request().expected_tenant_ref,
            prepared_context=source_case.context,
            operation=source_case.operation.model_dump(mode="json"),
        )
    )

    async def invoke():
        with capture_budget(budget):
            return await service.execute_native_capture(
                issuer=SETTINGS.clerk_issuer,
                subject=native.args["verified_clerk_subject"],
                clerk_session_id="session_invented_scribe",
                workload_client_id="11111111-1111-4111-8111-111111111111",
                data=request,
                key="invented-status-query",
                authority_settings=SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
                encounter_settings=ScribeEncounterSettings(enabled=True),
                client=DeadlineScribeClient(native.remote),
            )

    with pytest.raises((CaptureDeadlineExceeded, CaptureOperationUnconfirmed)):
        asyncio.run(invoke())
    assert (
        len(source_case.calls) == (1 if phase == "prepare" else 2)
        and engine.pool.checkedout() == 0
    )


@pytest.mark.parametrize(
    "fault", ["operation", "position", "generation", "extra_scope", "ordinary_action"]
)
def test_closed_prepared_source_rejects_other_mutations_and_resumes(
    native, source_case, fault
):
    value = source_case.operation.model_dump(mode="json")
    action = "prepared_source"
    if fault == "operation":
        value = {"kind": "creator_review"}
    elif fault == "position":
        value["payload"]["first_sequence_number"] = 1
    elif fault == "generation":
        value["payload"]["lease_generation"] = 2
    elif fault == "extra_scope":
        value["payload"]["session_id"] = str(uuid4())
    else:
        action = "operation"
    response = native.post(
        "creator_review",
        action=action,
        operation=value,
        prepared_context=source_case.context,
    )
    assert response.status_code == 422 and source_case.calls == []


def test_prepared_source_omits_new_exchange_but_preserves_original_age(
    native, engine, source_case, monkeypatch
):
    from app.services import authority

    # Ordinary source facade may exchange new context; prepared path must not.
    monkeypatch.setattr(
        authority,
        "exchange_context_token",
        lambda **_: pytest.fail("prepared source must reuse original context"),
    )
    before = []
    with Session(engine) as session:
        row = session.exec(
            select(MountContextToken).where(
                MountContextToken.token_hash
                == hashlib.sha256(
                    source_case.context["context_token"].encode()
                ).hexdigest()
            )
        ).one()
        before = [row.issued_at, row.expires_at]
    answer = source(native, source_case)
    assert answer.status_code == 200
    with Session(engine) as session:
        row = session.exec(
            select(MountContextToken).where(
                MountContextToken.token_hash
                == hashlib.sha256(
                    source_case.context["context_token"].encode()
                ).hexdigest()
            )
        ).one()
        assert [row.issued_at, row.expires_at] == before


def test_key_collision_cannot_dispatch_either_source_decision(native, source_case):
    source_case.operation = source_case.operation.model_copy(
        update={"authorize_idempotency_key": "invented-status-query"}
    )
    response = source(native, source_case)
    assert response.status_code == 409
    assert source_case.calls == []


def test_actual_admission_change_between_decisions_blocks_authorization(
    native, engine, source_case, monkeypatch
):
    from app.services import scribe_capture_gateway as gateway

    original = gateway.authorize_admitted_capture
    checks = []

    def current(*args):
        checks.append(True)
        if len(checks) == 3:
            mutate(native, engine, source_case.context, "consent")
        return original(*args)

    monkeypatch.setattr(gateway, "authorize_admitted_capture", current)
    response = source(native, source_case)
    assert response.status_code in {403, 409}
    assert len(checks) == 3 and len(source_case.calls) == 1
    assert "receipt" not in response.json() and engine.pool.checkedout() == 0


def test_final_encounter_change_is_rejected_after_both_valid_source_decisions(
    native, engine, source_case, monkeypatch
):
    from app.services import scribe_encounters

    original = scribe_encounters.encounter_workflow
    checks = []

    def changed(**kwargs):
        value = original(**kwargs)
        checks.append(True)
        if len(checks) == 2:
            value = dict(value)
            value["encounter"] = dict(
                value["encounter"], patient_ref="patient_00000000000000000000000001"
            )
        return value

    monkeypatch.setattr(scribe_encounters, "encounter_workflow", changed)
    response = source(native, source_case)
    assert response.status_code == 403 and len(source_case.calls) == 2
    assert "receipt" not in response.json() and engine.pool.checkedout() == 0


@pytest.mark.parametrize("action", ["mount", "preparation_context"])
def test_mount_advertises_separate_prepared_source_capability(monkeypatch, action):
    connection, _ = client(monkeypatch)
    changes = (
        {}
        if action == "mount"
        else {
            "binding_ref": "scribe_capture_binding_00000000000000000000000001",
            "preparation": "admission",
        }
    )
    response = connection.post(
        PATH, json=body(action=action, **changes), headers=headers()
    )
    assert response.status_code == 200
    assert response.headers["x-cosmaneura-prepared-source-start-version"] == "1"
