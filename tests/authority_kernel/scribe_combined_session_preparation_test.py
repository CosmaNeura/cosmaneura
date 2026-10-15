"""Real Core route/authority transactions; fictional downstream responses."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.routes import scribe_native_capture as route
from app.services.scribe_workflow import (
    PreparedConfiguredSessionReceipt,
    ScribeWorkflowRetryableError,
)

from app.models.authority import AuthorityActorExternalIdentity
from sqlmodel import Session, select

from tests.authority_kernel import scribe_admission_preparation_metadata_test as legacy
from tests.authority_kernel import scribe_preparation_metadata_test as f
from tests.authority_kernel.companion_provider_provisioning_test import (
    engine as _owned_engine,
)
from tests.authority_kernel.scribe_prepared_window_context_test import (
    new_mounted_context,
    prepared_admission,
)

native, workflow, remote_reads = f.native, f.workflow, f.remote_reads
engine = _owned_engine

# Run the existing legacy authority/revocation tests with this new disposable
# companion database too, rather than relying on the old fixed-address lab.
test_legacy_receipt = (
    legacy.test_combined_desktop_admission_returns_the_unchanged_full_receipt
)
test_legacy_default = (
    legacy.test_default_admission_does_not_read_or_publish_any_preparation
)
test_legacy_uncertainty = (
    legacy.test_uncertain_combined_metadata_never_publishes_partial_admission
)
test_legacy_cancel = (
    legacy.test_cancellation_after_metadata_read_cannot_publish_combined_reply
)


@pytest.fixture
def combined(native, remote_reads, monkeypatch):
    native.connection.app.dependency_overrides[route.get_capture_settings] = (
        lambda: ScribeCaptureSettings(
            enabled=True, combined_session_preparation_enabled=True
        )
    )
    calls = []

    async def create(tenant, payload, *, creator_actor_id, idempotency_key):
        calls.append(idempotency_key)
        receipt = await native.remote.create_configured_session(
            tenant,
            payload,
            creator_actor_id=creator_actor_id,
            idempotency_key=idempotency_key,
        )
        # This synthetic response builder represents the receiver's combined
        # reply; it is not another production HTTP dispatch.
        from app.services.scribe_capture_protocol import ReadOperation

        metadata = await remote_reads[1](
            tenant,
            receipt.id,
            ReadOperation(kind="preparation_metadata"),
            creator_actor_id=creator_actor_id,
        )
        remote_reads[0].clear()
        return PreparedConfiguredSessionReceipt(
            schema_version="1.0", admission=receipt, preparation_metadata=metadata
        )

    monkeypatch.setattr(
        native.remote, "create_prepared_configured_session", create, raising=False
    )
    return calls, create


def test_combined_removes_the_later_metadata_dispatch(
    native, engine, remote_reads, combined
):
    mounted = new_mounted_context(native)
    response = prepared_admission(native, mounted, include_preparation_metadata=True)
    assert response.status_code == 200, response.text
    assert len(combined[0]) == 1 and remote_reads[0] == []
    result = response.json()["result"]
    assert (
        result["preparation_metadata"]["review"]["session_id"] == result["session_id"]
    )
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("fault", ["lost", "revoked", "session", "metadata"])
def test_uncertain_or_revoked_combination_never_falls_back(
    native, engine, remote_reads, combined, monkeypatch, fault
):
    mounted = new_mounted_context(native)

    async def altered(*args, **kwargs):
        result = await combined[1](*args, **kwargs)
        assert engine.pool.checkedout() == 0
        if fault == "lost":
            raise ScribeWorkflowRetryableError("synthetic_lost_reply")
        if fault == "revoked":
            with Session(engine) as session:
                identity = session.exec(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                ).one()
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
                session.commit()
        elif fault == "session":
            result = result.model_copy(
                update={
                    "preparation_metadata": result.preparation_metadata.model_copy(
                        update={
                            "review": result.preparation_metadata.review.model_copy(
                                update={"session_id": uuid4()}
                            )
                        }
                    )
                }
            )
        else:
            result = result.model_copy(update={"preparation_metadata": None})
        return result

    monkeypatch.setattr(native.remote, "create_prepared_configured_session", altered)
    response = prepared_admission(native, mounted, include_preparation_metadata=True)
    assert response.status_code in (403, 409, 503), response.text
    assert "preparation_metadata" not in response.json()
    assert len(combined[0]) == 1 and remote_reads[0] == []


def test_no_metadata_opt_in_preserves_original_session_path(native, combined):
    mounted = new_mounted_context(native)
    response = prepared_admission(native, mounted)
    assert response.status_code == 200 and combined[0] == []


def test_phone_native_context_uses_combined_metadata_too(
    native, remote_reads, combined
):
    mounted = new_mounted_context(native)
    response = prepared_admission(native, mounted, include_native_preparation=True)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    context = result["native_recording_preparation"]["context"]
    assert context["session_id"] == result["session_id"]
    assert context["workspace_id"] == mounted["workspace_id"]
    assert "preparation_metadata" not in result
    assert len(combined[0]) == 1 and remote_reads[0] == []
