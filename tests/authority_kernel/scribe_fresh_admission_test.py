"""Real Core native route/authority/SQL; synthetic downstream only.

Authored for Root's separately reviewed owned-PG runner, not host-run here.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
from app.services.scribe_workflow import ScribeWorkflowRetryableError

from app.models.authority import AuthorityActorExternalIdentity, MountContextToken
from sqlmodel import Session, select

from tests.authority_kernel import scribe_preparation_metadata_test as fixtures
from tests.authority_kernel.companion_provider_provisioning_test import SETTINGS
from tests.authority_kernel.scribe_native_capture_receiver_test import body
from tests.authority_kernel.scribe_prepared_window_context_test import (
    new_mounted_context,
)

engine, native, workflow, remote_reads = (
    fixtures.engine,
    fixtures.native,
    fixtures.workflow,
    fixtures.remote_reads,
)


def fields(mounted, **changes):
    return (
        dict(
            action="fresh_admission",
            operation=None,
            prepared_context=None,
            workspace_id=mounted["workspace_id"],
            binding_ref=mounted["binding_ref"],
            retain_preparation_context=True,
            attestation={
                "modality": "in_person",
                "participant_regions": ["US-NY"],
                "method": "verbal_attestation",
                "all_parties_consented": True,
                "granted_at": datetime.now(UTC).isoformat(),
            },
            configured_capture={"schema_version": "1.0", "language_mode": "en-US"},
        )
        | changes
    )


def fresh(native, mounted, **changes):
    return native.post("creator_review", **fields(mounted, **changes))


def revoke_identity(native, engine):
    with Session(engine) as session:
        row = session.exec(
            select(AuthorityActorExternalIdentity).where(
                AuthorityActorExternalIdentity.subject
                == native.args["verified_clerk_subject"]
            )
        ).one()
        row.revoked_at = datetime.now(UTC)
        session.add(row)
        session.commit()


@pytest.mark.parametrize(
    "metadata", [None, "include_native_preparation", "include_preparation_metadata"]
)
def test_fresh_admission_runs_one_request_and_retains_private_original_context(
    native, engine, remote_reads, metadata
):
    mounted = new_mounted_context(native)
    old = mounted["prepared_context"]["context_token"]
    with Session(engine) as session:
        token = session.exec(
            select(MountContextToken).where(
                MountContextToken.token_hash == hashlib.sha256(old.encode()).hexdigest()
            )
        ).one()
        token.issued_at = datetime.now(UTC) - timedelta(seconds=60)
        session.add(token)
        session.commit()
    before = len(native.remote.consents), len(native.remote.sessions)
    remote_reads[0].clear()
    answer = fresh(native, mounted, **({metadata: True} if metadata else {}))
    assert answer.status_code == 200, answer.text
    result = answer.json()["result"]
    assert (
        result["status"] == "admitted"
        and result["workspace_id"] == mounted["workspace_id"]
    )
    assert (len(native.remote.consents), len(native.remote.sessions)) == (
        before[0] + 1,
        before[1] + 1,
    )
    context = result["prepared_context"]
    assert (
        set(context) == {"context_token", "interaction_ref", "client_turn_id"}
        and context["context_token"] != old
    )
    if metadata == "include_native_preparation":
        assert (
            result["native_recording_preparation"]["context"]["session_id"]
            == result["session_id"]
        )
    elif metadata:
        assert (
            result["preparation_metadata"]["review"]["session_id"]
            == result["session_id"]
        )
    else:
        assert remote_reads[0] == []
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"attestation": None},
        {"configured_capture": None},
        {"retain_preparation_context": None},
        {
            "prepared_context": {
                "context_token": "invented-context-token",
                "interaction_ref": "invented-interaction-1234",
                "client_turn_id": str(uuid4()),
            }
        },
        {"operation": {"kind": "creator_review"}},
        {"preparation": "admission"},
        {"include_preparation_metadata": True, "include_native_preparation": True},
    ],
)
def test_compound_schema_does_not_admit_without_fresh_closed_consent(native, changes):
    mounted = new_mounted_context(native)
    before = len(native.remote.consents), len(native.remote.sessions)
    answer = fresh(native, mounted, **changes)
    assert answer.status_code == 422
    assert (len(native.remote.consents), len(native.remote.sessions)) == before


def test_revocation_after_consent_is_journaled_but_cannot_dispatch_session(
    native, engine, monkeypatch
):
    mounted = new_mounted_context(native)
    original = native.remote.create_consent
    before = len(native.remote.sessions)

    async def returned_then_revoked(*args, **kwargs):
        value = await original(*args, **kwargs)
        assert engine.pool.checkedout() == 0
        revoke_identity(native, engine)
        return value

    monkeypatch.setattr(native.remote, "create_consent", returned_then_revoked)
    response = fresh(native, mounted)
    assert response.status_code == 403 and len(native.remote.sessions) == before
    assert "prepared_context" not in response.text and engine.pool.checkedout() == 0


@pytest.mark.parametrize("fault", ["revocation", "loss", "wrong_session"])
def test_metadata_error_or_late_revocation_never_publishes_partial_compound(
    native, engine, remote_reads, monkeypatch, fault
):
    mounted = new_mounted_context(native)
    calls, original = remote_reads
    calls.clear()

    async def altered(*args, **kwargs):
        value = await original(*args, **kwargs)
        assert engine.pool.checkedout() == 0
        if fault == "revocation":
            revoke_identity(native, engine)
        elif fault == "loss":
            raise ScribeWorkflowRetryableError("scribe_transport_unavailable")
        else:
            value = value.model_copy(
                update={
                    "review": value.review.model_copy(update={"session_id": uuid4()})
                }
            )
        return value

    monkeypatch.setattr(native.remote, "capture_operation", altered)
    response = fresh(native, mounted, include_preparation_metadata=True)
    assert (
        response.status_code
        == {"revocation": 403, "loss": 503, "wrong_session": 409}[fault]
    )
    assert "prepared_context" not in response.text and "session_id" not in response.text
    assert calls == ["preparation_metadata"] and engine.pool.checkedout() == 0


def test_cancel_after_metadata_read_releases_worker_without_public_context(
    native, engine, remote_reads, monkeypatch
):
    mounted = new_mounted_context(native)
    calls, original = remote_reads
    calls.clear()
    budget = CaptureBudget(30)

    async def cancel(*args, **kwargs):
        value = await original(*args, **kwargs)
        budget.cancel()
        return value

    monkeypatch.setattr(native.remote, "capture_operation", cancel)
    data = service.NativeCaptureRequest.model_validate(
        body(
            **fields(mounted, include_preparation_metadata=True),
            expected_actor_ref=native.request().expected_actor_ref,
            expected_tenant_ref=native.request().expected_tenant_ref,
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
                key="invented-compound-cancel",
                authority_settings=SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
                encounter_settings=ScribeEncounterSettings(enabled=True),
                client=DeadlineScribeClient(native.remote),
            )

    with pytest.raises((CaptureDeadlineExceeded, CaptureOperationUnconfirmed)):
        asyncio.run(invoke())
    assert calls == ["preparation_metadata"] and engine.pool.checkedout() == 0
