"""Existing public-key read through actual native Core on owned synthetic PG."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.routes import scribe_capture_deadline as boundary
from app.services import scribe_capture_protocol as protocol
from app.services import scribe_native_capture as service

from activemodel import session_manager
from app.models.authority import AuthorityActorExternalIdentity
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Session, select

from tests.authority_kernel.scribe_native_status_enrichment_test import (
    engine as _engine,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    native as _native,
)
from tests.authority_kernel.scribe_native_status_enrichment_test import (
    workflow as _workflow,
)

engine, native, workflow = _engine, _native, _workflow


def public_key():
    return protocol.EncryptionKey(
        key_version="synthetic-key",
        algorithm="RSA-OAEP-256",
        public_jwk=protocol.PublicJwk(
            kty="RSA",
            n="a" * 512,
            e="AQAB",
            alg="RSA-OAEP-256",
            ext=True,
            key_ops=("wrapKey",),
        ),
    )


def remote_key(native, engine, monkeypatch, after=None, malformed=None):
    previous = native.remote.capture_operation
    calls = []

    async def read(tenant_id, session_id, operation, **kwargs):
        assert session_manager._session_context.get() is None
        assert engine.pool.checkedout() == 0
        calls.append(operation.kind)
        if operation.kind == "encryption_key":
            assert tenant_id == native.remote.tenant
            result = (
                public_key()
                if malformed is None
                else SimpleNamespace(model_dump=lambda **_: malformed)
            )
        else:
            result = await previous(tenant_id, session_id, operation, **kwargs)
        if after is not None:
            await after(operation.kind)
        return result

    monkeypatch.setattr(native.remote, "capture_operation", read)
    return calls


def test_public_key_read_preserves_existing_review_and_closed_public_receipt(
    native, engine, monkeypatch
):
    calls = remote_key(native, engine, monkeypatch)
    result = native.post("encryption_key")
    assert result.status_code == 200
    receipt = result.json()["result"]["receipt"]
    assert receipt == public_key().model_dump(mode="json")
    assert calls == ["creator_review", "encryption_key"]
    assert set(receipt) == {"key_version", "algorithm", "public_jwk"}
    assert set(receipt["public_jwk"]) == {"kty", "n", "e", "alg", "ext", "key_ops"}
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("phase", ["creator_review", "encryption_key", "final_lookup"])
@pytest.mark.parametrize("change", ["identity", "binding"])
def test_committed_revocation_prevents_public_key_publication(
    native, engine, monkeypatch, phase, change
):
    def revoke():
        with Session(engine) as session:
            if change == "identity":
                row = session.exec(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject
                        == native.args["verified_clerk_subject"]
                    )
                ).one()
            else:
                row = session.exec(
                    select(ScribeCaptureBinding).where(
                        ScribeCaptureBinding.workspace_id
                        == UUID(native.row["workspace_id"])
                    )
                ).one()
            row.revoked_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    async def after(kind):
        if kind == phase:
            revoke()

    calls = remote_key(native, engine, monkeypatch, after=after)
    if phase == "final_lookup":
        previous = service.execute_capture_operation

        async def changed(**kwargs):
            result = await previous(**kwargs)
            revoke()
            return result

        monkeypatch.setattr(service, "execute_capture_operation", changed)
    response = native.post("encryption_key")
    assert response.status_code == 403
    assert "receipt" not in response.json()
    assert calls == (
        ["creator_review"]
        if phase == "creator_review"
        else ["creator_review", "encryption_key"]
    )
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("extra", ["d", "p", "q", "wrapped_key", "secret"])
def test_private_or_extra_key_fields_are_never_published(
    native, engine, monkeypatch, extra
):
    receipt = public_key().model_dump(mode="json")
    receipt["public_jwk"][extra] = "private-synthetic-sentinel"
    calls = remote_key(native, engine, monkeypatch, malformed=receipt)
    response = native.post("encryption_key")
    assert response.status_code == 409
    assert "private-synthetic-sentinel" not in response.text
    assert "receipt" not in response.json()
    assert calls == ["creator_review", "encryption_key"]


def test_cancelled_public_key_read_cannot_publish_or_retain_transaction(
    native, engine, monkeypatch
):
    settled = []

    async def after(kind):
        if kind == "encryption_key":
            try:
                await asyncio.sleep(30)
            finally:
                settled.append(True)

    remote_key(native, engine, monkeypatch, after=after)
    # Leave the real SQL/authorization phases time to reach the remote key
    # read; this case tests cancellation there, not before dispatch.
    monkeypatch.setattr(boundary, "CAPTURE_REQUEST_SECONDS", 2)
    response = native.post("encryption_key")
    assert response.status_code == 504
    assert "receipt" not in response.json()
    assert settled == [True]
    assert engine.pool.checkedout() == 0
