"""Combined desktop admission with real Core authority/SQL and invented Scribe."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

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
from app.services.scribe_workflow import ScribeWorkflowRetryableError

from app.models.authority import AuthorityActorExternalIdentity, MountContextToken
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import select
from sqlmodel import Session

from tests.authority_kernel import scribe_preparation_metadata_test as fixtures
from tests.authority_kernel.companion_provider_provisioning_test import SETTINGS
from tests.authority_kernel.scribe_native_capture_receiver_test import (
    PATH,
    body,
    headers,
)
from tests.authority_kernel.scribe_native_capture_receiver_test import (
    client as native_http_client,
)
from tests.authority_kernel.scribe_prepared_window_context_test import (
    new_mounted_context,
    prepared_admission,
)

engine, native, workflow, remote_reads = (
    fixtures.engine,
    fixtures.native,
    fixtures.workflow,
    fixtures.remote_reads,
)


def test_combined_desktop_admission_returns_the_unchanged_full_receipt(
    native, engine, remote_reads
):
    mounted = new_mounted_context(native)
    remote_reads[0].clear()
    before = len(native.remote.consents), len(native.remote.sessions)
    answer = prepared_admission(native, mounted, include_preparation_metadata=True)
    assert answer.status_code == 200, answer.text
    assert answer.headers["x-cosmaneura-admission-preparation-metadata-version"] == "1"
    result = answer.json()["result"]
    assert (len(native.remote.consents), len(native.remote.sessions)) == (
        before[0] + 1,
        before[1] + 1,
    )
    assert remote_reads[0] == ["preparation_metadata"]
    separate = native.post(
        "preparation_metadata",
        workspace_id=mounted["workspace_id"],
        binding_ref=mounted["binding_ref"],
    )
    assert separate.status_code == 200, separate.text
    assert result["preparation_metadata"] == separate.json()["result"]["receipt"]
    assert (
        result["preparation_metadata"]["review"]["session_id"] == result["session_id"]
    )
    assert set(result["preparation_metadata"]) == {
        "schema_version",
        "review",
        "encryption_key",
        "capabilities",
    }
    assert "native_recording_preparation" not in result
    assert mounted["prepared_context"]["context_token"] not in answer.text
    assert engine.pool.checkedout() == 0


def test_default_admission_does_not_read_or_publish_any_preparation(
    native, remote_reads
):
    mounted = new_mounted_context(native)
    remote_reads[0].clear()
    answer = prepared_admission(native, mounted)
    assert answer.status_code == 200, answer.text
    assert set(answer.json()["result"]) == {
        "contract_version",
        "operation_ref",
        "binding_ref",
        "workspace_id",
        "appointment_ref",
        "consent_record_id",
        "session_id",
        "status",
        "configured_capture",
        "request_nonce",
        "checked_at",
    }
    assert remote_reads[0] == []


@pytest.mark.parametrize(
    "fault",
    [
        "revocation",
        "provider_link",
        "expired_context",
        "revoked_context",
        "reply_loss",
        "wrong_session",
        "malformed",
    ],
)
def test_uncertain_combined_metadata_never_publishes_partial_admission(
    native, engine, remote_reads, monkeypatch, fault
):
    mounted = new_mounted_context(native)
    before = len(native.remote.consents), len(native.remote.sessions)
    calls, original = remote_reads
    calls.clear()

    async def altered(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert engine.pool.checkedout() == 0
        if fault == "reply_loss":
            raise ScribeWorkflowRetryableError()
        elif fault == "revocation":
            with Session(engine) as session:
                identity = session.execute(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                ).scalar_one()
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
                session.commit()
        elif fault == "provider_link":
            with Session(engine) as session:
                link = session.get(
                    ScribeCaptureProviderLink, native.provisioned.provider_link_ref
                )
                link.revoked_at = datetime.now(UTC)
                session.add(link)
                session.commit()
        elif fault in {"expired_context", "revoked_context"}:
            with Session(engine) as session:
                context = session.execute(
                    select(MountContextToken).where(
                        MountContextToken.token_hash
                        == hashlib.sha256(
                            mounted["prepared_context"]["context_token"].encode()
                        ).hexdigest()
                    )
                ).scalar_one()
                if fault == "expired_context":
                    context.issued_at = datetime.now(UTC) - timedelta(seconds=31)
                else:
                    context.revoked_at = datetime.now(UTC)
                session.add(context)
                session.commit()
        elif fault == "wrong_session":
            result = result.model_copy(
                update={
                    "review": result.review.model_copy(update={"session_id": uuid4()})
                }
            )
        else:
            result = result.model_copy(
                update={"encryption_key": {"private_key": "invented"}}
            )
        return result

    monkeypatch.setattr(native.remote, "capture_operation", altered)
    answer = prepared_admission(native, mounted, include_preparation_metadata=True)
    assert (
        answer.status_code
        == {
            "revocation": 403,
            "provider_link": 403,
            "expired_context": 403,
            "revoked_context": 403,
            "reply_loss": 503,
            "wrong_session": 409,
            "malformed": 409,
        }[fault]
    ), answer.text
    assert "preparation_metadata" not in answer.text and "session_id" not in answer.text
    assert calls == ["preparation_metadata"]
    assert (len(native.remote.consents), len(native.remote.sessions)) == (
        before[0] + 1,
        before[1] + 1,
    )
    assert engine.pool.checkedout() == 0


def test_cancellation_after_metadata_read_cannot_publish_combined_reply(
    native, engine, remote_reads, monkeypatch
):
    mounted = new_mounted_context(native)
    calls, original = remote_reads
    calls.clear()
    budget = CaptureBudget(30)

    async def cancelled(*args, **kwargs):
        result = await original(*args, **kwargs)
        budget.cancel()
        return result

    monkeypatch.setattr(native.remote, "capture_operation", cancelled)
    data = service.NativeCaptureRequest.model_validate(
        body(
            action="prepared_admission",
            workspace_id=mounted["workspace_id"],
            binding_ref=mounted["binding_ref"],
            prepared_context=mounted["prepared_context"],
            attestation={
                "modality": "in_person",
                "participant_regions": ["US-NY"],
                "method": "verbal_attestation",
                "all_parties_consented": True,
                "granted_at": datetime.now(UTC).isoformat(),
            },
            configured_capture={"schema_version": "1.0", "language_mode": "en-US"},
            expected_actor_ref=native.request().expected_actor_ref,
            expected_tenant_ref=native.request().expected_tenant_ref,
            include_preparation_metadata=True,
        )
    )

    async def invoke():
        with capture_budget(budget):
            return await service.execute_native_capture(
                issuer=SETTINGS.clerk_issuer,
                subject=native.args["verified_clerk_subject"],
                clerk_session_id="session_invented_scribe",
                workload_client_id="11111111-1111-4111-8111-111111111111",
                data=data,
                key="invented-desktop-combined-cancel",
                authority_settings=SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
                encounter_settings=ScribeEncounterSettings(enabled=True),
                client=DeadlineScribeClient(native.remote),
            )

    with pytest.raises((CaptureDeadlineExceeded, CaptureOperationUnconfirmed)):
        asyncio.run(invoke())
    assert calls == ["preparation_metadata"] and engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "action",
    [
        "mount",
        "operation",
        "preparation_context",
        "prepared_window",
        "prepared_native_read",
    ],
)
def test_desktop_preparation_opt_in_is_closed_to_every_other_action(action):
    with pytest.raises(ValidationError, match="unexpected_preparation_metadata"):
        service.NativeCaptureRequest.model_validate(
            body(action=action, include_preparation_metadata=True)
        )


def test_desktop_and_native_preparation_opt_ins_cannot_be_combined():
    with pytest.raises(ValidationError, match="conflicting_admission_preparation"):
        service.NativeCaptureRequest.model_validate(
            prepared_request(
                include_native_preparation=True,
                include_preparation_metadata=True,
            )
        )


def prepared_request(**changes):
    return body(
        action="prepared_admission",
        binding_ref="scribe_capture_binding_00000000000000000000000001",
        prepared_context={
            "context_token": "invented-context-token-for-test",
            "interaction_ref": "invented-interaction-for-test",
            "client_turn_id": str(uuid4()),
        },
        attestation={
            "modality": "in_person",
            "participant_regions": ["US-NY"],
            "method": "verbal_attestation",
            "all_parties_consented": True,
            "granted_at": datetime.now(UTC).isoformat(),
        },
        configured_capture={"schema_version": "1.0", "language_mode": "en-US"},
        **changes,
    )


@pytest.mark.parametrize("value", [True, None])
def test_desktop_preparation_opt_in_accepts_true_or_absence(value):
    value = service.NativeCaptureRequest.model_validate(
        prepared_request(include_preparation_metadata=value)
    )
    assert value.action == "prepared_admission"


@pytest.mark.parametrize("value", [False, "true", 1, [], {}])
def test_desktop_preparation_opt_in_accepts_only_true(value):
    with pytest.raises(ValidationError, match="invalid_preparation_metadata_opt_in"):
        service.NativeCaptureRequest.model_validate(
            prepared_request(include_preparation_metadata=value)
        )


@pytest.mark.parametrize("action", ["mount", "preparation_context"])
def test_mount_and_preparation_context_advertise_full_metadata_support(
    monkeypatch, action
):
    connection, _ = native_http_client(monkeypatch)
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
    assert (
        response.headers["x-cosmaneura-admission-preparation-metadata-version"] == "1"
    )
    assert response.headers["x-cosmaneura-admission-native-preparation-version"] == "1"
    assert response.headers["cache-control"] == "no-store"
