"""Real Core authority/journals with deterministic Scribe fault injection."""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.services import authority
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as p
from app.services.scribe_workflow import (
    CaptureSession,
    CaptureTranscript,
    CaptureTranscriptionStatus,
    ScribeWorkflowRetryableError,
)

from app.models.authority import AuthorityActorExternalIdentity
from sqlalchemy import text
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_admission_test import (
    FakeScribe,
    call_arguments,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    capture_engine as _capture_engine,
)

admission_case = _admission_case
capture_engine = _capture_engine


def pair(kind, scope):
    now = datetime.now(UTC)
    obj, lease = uuid4(), uuid4()
    aad = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "tenant_id": str(scope.tenant_id),
                    "session_id": str(scope.session_id),
                    "object_id": str(obj),
                    "sequence_number": 0,
                    "channel": "microphone",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    reservation = {
        "protocol_version": "1.0",
        "object_id": obj,
        "session_id": scope.session_id,
        "channel": "microphone",
        "sequence_number": 0,
        "additional_authenticated_data": aad,
        "created_at": now,
    }
    payload: dict[str, Any] = {"protocol_version": "1.0"}
    op = {"kind": kind}
    if kind == "encryption_key":
        response = p.EncryptionKey(
            key_version="synthetic-key",
            algorithm="RSA-OAEP-256",
            public_jwk=p.PublicJwk(
                kty="RSA",
                n="a" * 512,
                e="AQAB",
                alg="RSA-OAEP-256",
                ext=True,
                key_ops=("wrapKey",),
            ),
        )
    elif kind == "capabilities":
        response = p.Capabilities(
            media=p.MediaCapabilities(
                media_type="audio/wav",
                encoding="pcm_s16le",
                channels=1,
                sample_rate_hz=16000,
            ),
            target_chunk_duration_ms=10000,
            max_envelope_bytes=327680,
            max_chunks=1000,
            remaining_aggregate_plaintext_bytes=67108864,
            capture_lease_ttl_seconds=900,
            reservation_window_seconds=900,
            max_reservations_per_batch=90,
            upload_concurrency=2,
        )
    elif kind == "transcription_status":
        response = CaptureTranscriptionStatus(
            session_id=scope.session_id,
            status="completed",
            transcript_available=True,
            updated_at=now,
        )
    elif kind == "transcript":
        response = CaptureTranscript.model_validate(
            {
                "id": uuid4(),
                "session_id": scope.session_id,
                "created_at": now,
                "language_code": "en",
                "stt_provider": "synthetic",
                "segments": [
                    {
                        "content": "Invented visit for a recorder test.",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "speaker_id": "speaker-1",
                        "speaker_role": "clinician",
                        "confidence": None,
                    }
                ],
            }
        )
    elif kind == "bind_key":
        op["payload"] = {"key_version": "synthetic-key", "wrapped_dek": "a" * 512}
        response = p.KeyReceipt(
            session_id=scope.session_id, key_version="synthetic-key", created_at=now
        )
    elif kind in {"issue_lease", "renew_lease"}:
        if kind == "renew_lease":
            op["lease_id"] = lease
            payload["expected_generation"] = 1
        op["payload"] = payload
        response = p.LeaseReceipt(
            receipt_id=uuid4(),
            lease_id=lease,
            session_id=scope.session_id,
            generation=2 if kind == "renew_lease" else 1,
            issued_at=now,
            expires_at=now + timedelta(minutes=15),
        )
    elif kind == "reserve_chunks":
        op["payload"] = {
            **payload,
            "capture_lease_id": lease,
            "capture_lease_generation": 1,
            "channel": "microphone",
            "first_sequence_number": 0,
            "count": 1,
        }
        response = p.ReservationReceipt(
            batch_id=uuid4(),
            session_id=scope.session_id,
            capture_authorized_until=now + timedelta(minutes=15),
            reservations=(p.Reservation(**reservation),),
        )
    elif kind == "authorize_upload":
        op.update(object_id=obj, payload=payload)
        response = p.UploadReceipt(
            authorization_receipt_id=uuid4(),
            object_id=obj,
            additional_authenticated_data=aad,
            upload_url=f"https://storage.invalid/tenants/{scope.tenant_id}/sessions/{scope.session_id}/microphone/00000000-{obj}.enc?synthetic=true",
            expires_at=now + timedelta(minutes=15),
        )
    elif kind == "chunk_receipt":
        op["object_id"] = obj
        response = p.ChunkReceipt(
            reservation=p.Reservation(**reservation),
            state="reserved",
            registration=None,
        )
    elif kind == "register_chunk":
        op["payload"] = {
            "object_id": obj,
            "sequence_number": 0,
            "channel": "microphone",
            "sha256": "a" * 64,
            "ciphertext_size_bytes": 320000,
        }
        response = p.RegisteredChunk(
            id=uuid4(), session_id=scope.session_id, registered_at=now, **op["payload"]
        )
    else:
        op["payload"] = {
            **payload,
            "capture_lease_id": lease,
            "capture_lease_generation": 1,
            "channels": [{"channel": "microphone", "final_sequence_number": 0}],
        }
        response = CaptureSession(
            id=scope.session_id,
            created_at=now,
            status="uploaded",
            **scope.session_request.model_dump(),
        )
    return p.OPERATION_ADAPTER.validate_python(op), response


KINDS = [
    "encryption_key",
    "capabilities",
    "bind_key",
    "issue_lease",
    "renew_lease",
    "reserve_chunks",
    "authorize_upload",
    "chunk_receipt",
    "register_chunk",
    "complete",
    "transcription_status",
    "transcript",
]


class NativeScribe(FakeScribe):
    def __init__(self):
        super().__init__()
        self.operation_calls = []
        self.receipts = {}
        self.after_native = lambda _: None
        self.lose = False
        self.result = None

    async def get_capture_status(self, tenant_id, session_id):
        self.after_native("status")
        available = isinstance(self.result, CaptureTranscript)
        return CaptureTranscriptionStatus(
            session_id=session_id,
            status="completed" if available else "not_requested",
            transcript_available=available,
            updated_at=datetime.now(UTC),
        )

    async def capture_operation(
        self, tenant_id, session_id, operation, *, idempotency_key
    ):
        self.operation_calls.append((tenant_id, session_id, operation, idempotency_key))
        result = self.receipts.setdefault(idempotency_key, self.result)
        assert result is not None
        self.after_native("operation")
        if self.lose:
            self.lose = False
            raise ScribeWorkflowRetryableError("private_sentinel")
        return result


async def ready(fixture):
    fixture.client = NativeScribe()
    await admission.admit_capture(**call_arguments(fixture))
    return admission.authorize_admitted_capture(
        fixture.caller, fixture.binding.binding_ref, fixture.binding.workspace_id
    )


def arguments(fixture, operation, key=None):
    return {
        "caller": fixture.caller,
        "binding_ref": fixture.binding.binding_ref,
        "workspace_id": fixture.binding.workspace_id,
        "operation": operation,
        "client_request_key": key or uuid4().hex,
        "client": fixture.client,
        "upload_origins": ("https://storage.invalid",),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_gateway_uses_admitted_scope_and_preserves_protocol_receipt(
    admission_case, kind
):
    f = admission_case
    scope = await ready(f)
    operation, f.client.result = pair(kind, scope)
    result = await gateway.execute_capture_operation(**arguments(f, operation))
    assert result == f.client.result
    assert f.client.operation_calls[0][:2] == (scope.tenant_id, scope.session_id)
    assert "private_sentinel" not in repr(result)


@pytest.mark.asyncio
async def test_lost_committed_response_retries_same_remote_operation_after_reopen(
    admission_case,
):
    f = admission_case
    scope = await ready(f)
    operation, f.client.result = pair("issue_lease", scope)
    f.client.lose = True
    args = arguments(f, operation)
    with pytest.raises(gateway.CaptureOperationUnconfirmed) as error:
        await gateway.execute_capture_operation(**args)
    assert not str(error.value) and error.value.__cause__ is None
    from activemodel.session_manager import _session_context

    with Session(f.engine) as fresh:
        token = _session_context.set(fresh)
        try:
            assert await gateway.execute_capture_operation(**args) == f.client.result
        finally:
            _session_context.reset(token)
    assert len(f.client.receipts) == 1
    assert f.client.operation_calls[0][3] == f.client.operation_calls[1][3]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["status", "operation"])
async def test_revocation_during_remote_call_denies_next_action_or_receipt_without_holding_locks(
    admission_case, phase
):
    f = admission_case
    scope = await ready(f)
    operation, f.client.result = pair("issue_lease", scope)

    def revoke(observed):
        if observed != phase:
            return
        with Session(f.engine) as s:
            s.execute(text("SET LOCAL lock_timeout = '1s'"))
            identity = s.get(AuthorityActorExternalIdentity, f.case.identity.id)
            assert identity is not None
            identity.revoked_at = datetime.now(UTC)
            s.add(identity)
            s.commit()

    f.client.after_native = revoke
    with pytest.raises(authority.AuthorityKernelError):
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert len(f.client.operation_calls) == (1 if phase == "operation" else 0)
    assert len(f.client.receipts) == (1 if phase == "operation" else 0)


@pytest.mark.asyncio
async def test_no_admission_or_wrong_workspace_never_calls_remote(admission_case):
    f = admission_case
    f.client = NativeScribe()
    op = p.ReadOperation(kind="capabilities")
    with pytest.raises(admission.AdmissionConsentRequired):
        await gateway.execute_capture_operation(**arguments(f, op))
    await ready(f)
    with pytest.raises(authority.AuthorityKernelError):
        await gateway.execute_capture_operation(
            **{**arguments(f, op), "workspace_id": uuid4()}
        )
    assert not f.client.operation_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "session",
        "aad",
        "target",
        "object",
        "expired",
        "sequence",
        "key",
        "generation",
        "completion",
    ],
)
async def test_gateway_rejects_misbound_or_expired_native_receipts(
    admission_case, mutation
):
    f = admission_case
    scope = await ready(f)
    kind = {
        "session": "bind_key",
        "aad": "authorize_upload",
        "target": "authorize_upload",
        "object": "chunk_receipt",
        "expired": "issue_lease",
        "sequence": "reserve_chunks",
        "key": "bind_key",
        "generation": "renew_lease",
        "completion": "complete",
    }[mutation]
    op, receipt = pair(kind, scope)
    data = receipt.model_dump(mode="json")
    if mutation == "session":
        data["session_id"] = str(uuid4())
    elif mutation == "aad":
        data["additional_authenticated_data"] = "e30"
    elif mutation == "target":
        data["upload_url"] = data["upload_url"].replace(
            "storage.invalid", "unapproved.invalid"
        )
    elif mutation == "object":
        data["reservation"]["object_id"] = str(uuid4())
    elif mutation == "expired":
        data["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif mutation == "sequence":
        data["reservations"][0]["sequence_number"] = 1
    elif mutation == "key":
        data["key_version"] = "other-key"
    elif mutation == "generation":
        data["generation"] = 3
    else:
        data["patient_id"] = "foreign-synthetic-person"
    f.client.result = type(receipt).model_validate(data)
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await gateway.execute_capture_operation(**arguments(f, op))
