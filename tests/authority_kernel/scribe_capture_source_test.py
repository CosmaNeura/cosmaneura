"""Qualified source projections and current Core authority, without live services."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.services import authority
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_recovery_contract import (
    CaptureRecoveryHistory,
    CaptureRecoveryReceipt,
)
from app.services.scribe_capture_working_contract import (
    SegmentCorrection,
    TranscriptCorrection,
    canonical_hash,
)
from app.services.scribe_workflow import CaptureSource, CaptureTranscript

from app.models.authority import AuthorityActorExternalIdentity
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_client_test import client_for
from tests.authority_kernel.scribe_capture_gateway_test import (
    admission_case,
    arguments,
    capture_engine,
)
from tests.authority_kernel.scribe_capture_recovery_test import pair as recovery_pair
from tests.authority_kernel.scribe_capture_recovery_test import setup as setup_recovery
from tests.authority_kernel.scribe_capture_working_test import working_receipt

__all__ = ["admission_case", "capture_engine"]
FIXTURE = Path(__file__).parent / "fixtures/capture_source.json"


def envelope(raw, working, history):
    recovery_hash = canonical_hash(history)
    return CaptureSource(
        session_id=raw.session_id,
        raw_transcript=raw,
        working_transcript=working,
        recovery_history=history,
        recovery_sha256=recovery_hash,
        capture_incomplete=bool(history.recoveries),
        source_sha256=canonical_hash(
            {
                "raw_transcript_sha256": working.raw_transcript_sha256,
                "working_sha256": working.working_sha256,
                "recovery_sha256": recovery_hash,
            }
        ),
    )


async def setup(f, kind="capture_source"):
    scope = await setup_recovery(f)
    f.caller = replace(
        f.caller,
        capture_settings=replace(
            f.caller.capture_settings, source_envelopes_enabled=True
        ),
    )
    raw = CaptureTranscript.model_validate(
        json.loads(FIXTURE.read_text())["base"]["raw_transcript"]
    ).model_copy(update={"session_id": scope.session_id})
    first = working_receipt(raw, scope.session_request.practitioner_id)
    _, receipt = recovery_pair(scope)
    assert isinstance(receipt, CaptureRecoveryReceipt)
    history = CaptureRecoveryHistory(session_id=scope.session_id, recoveries=(receipt,))
    if kind == "capture_source":
        f.client.result = envelope(raw, first, history)
        operation = protocol.ReadOperation(kind="capture_source")
    else:
        second = working_receipt(
            raw,
            scope.session_request.practitioner_id,
            version=2,
            content="Invented source correction.",
        ).model_copy(update={"previous_working_sha256": first.working_sha256})
        f.client.result = envelope(raw, second, history)
        operation = protocol.CorrectCaptureSource(
            kind="correct_capture_source",
            payload=TranscriptCorrection(
                raw_transcript_id=raw.id,
                raw_transcript_sha256=first.raw_transcript_sha256,
                expected_working_version=1,
                expected_working_sha256=first.working_sha256,
                corrections=tuple(
                    SegmentCorrection(
                        source_segment_id=part.source_segment_id,
                        content=part.content,
                        speaker_role=part.speaker_role,
                    )
                    for part in second.working_source.segments
                ),
            ),
        )
    return scope, operation


def test_consumer_agrees_with_actual_scribe_envelopes():
    values = json.loads(FIXTURE.read_text())
    for name in ("base", "second"):
        source = CaptureSource.model_validate(values[name])
        assert source.model_dump(mode="json") == values[name]
        assert source.capture_incomplete
        source.validate_practitioner(
            source.recovery_history.recoveries[0].acknowledged_by_practitioner_id
        )
    assert values["base"]["recovery_sha256"] == values["second"]["recovery_sha256"]
    assert values["base"]["source_sha256"] != values["second"]["source_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["capture_source", "correct_capture_source"])
async def test_source_ops_use_admitted_practitioner_and_exact_scope(
    admission_case, kind
):
    f = admission_case
    scope, operation = await setup(f, kind)
    result = await gateway.execute_capture_operation(**arguments(f, operation))
    assert result == f.client.result
    assert f.client.practitioner == scope.session_request.practitioner_id
    assert len(f.client.operation_calls) == 1


@pytest.mark.asyncio
async def test_source_flag_is_independent_and_dark(admission_case):
    f = admission_case
    _, operation = await setup(f)
    f.caller = replace(
        f.caller,
        capture_settings=replace(
            f.caller.capture_settings, source_envelopes_enabled=False
        ),
    )
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert not f.client.operation_calls


@pytest.mark.asyncio
async def test_source_correction_recovers_one_namespaced_key(admission_case):
    f = admission_case
    _, operation = await setup(f, "correct_capture_source")
    assert isinstance(operation, protocol.CorrectCaptureSource)
    f.client.lose = True
    args = arguments(f, operation)
    with pytest.raises(gateway.CaptureOperationUnconfirmed):
        await gateway.execute_capture_operation(**args)
    assert await gateway.execute_capture_operation(**args) == f.client.result
    assert f.client.operation_calls[0] == f.client.operation_calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["capture_source", "correct_capture_source"])
@pytest.mark.parametrize("phase", ["status", "operation"])
async def test_revocation_during_source_call_blocks_disclosure(
    admission_case, kind, phase
):
    f = admission_case
    _, operation = await setup(f, kind)

    def revoke(observed):
        if observed == phase:
            with Session(f.engine) as session:
                row = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
                assert row is not None
                row.revoked_at = datetime.now(UTC)
                session.add(row)
                session.commit()

    f.client.after_native = revoke
    with pytest.raises(authority.AuthorityKernelError) as error:
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert "Invented" not in str(error.value)
    assert len(f.client.operation_calls) == (0 if phase == "status" else 1)


@pytest.mark.asyncio
async def test_self_consistent_foreign_practitioner_manifest_is_not_admitted(
    admission_case,
):
    f = admission_case
    _, operation = await setup(f)
    source = f.client.result
    history = source.recovery_history.model_copy(
        update={
            "recoveries": tuple(
                item.model_copy(update={"acknowledged_by_practitioner_id": "another"})
                for item in source.recovery_history.recoveries
            )
        }
    )
    f.client.result = envelope(
        source.raw_transcript, source.working_transcript, history
    )
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await gateway.execute_capture_operation(**arguments(f, operation))


@pytest.mark.asyncio
async def test_correction_response_must_contain_the_requested_change(admission_case):
    f = admission_case
    _, operation = await setup(f, "correct_capture_source")
    assert isinstance(operation, protocol.CorrectCaptureSource)
    payload = operation.payload.model_dump(mode="json")
    payload["corrections"][0]["content"] = "A different requested correction."
    changed = protocol.CorrectCaptureSource(
        kind="correct_capture_source",
        payload=TranscriptCorrection.model_validate(payload),
    )
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await gateway.execute_capture_operation(**arguments(f, changed))


@pytest.mark.parametrize(
    "field",
    ["raw_transcript", "recovery_sha256", "source_sha256", "capture_incomplete"],
)
def test_modified_source_projection_is_rejected(field):
    value = json.loads(FIXTURE.read_text())["base"]
    if field == "raw_transcript":
        value[field]["segments"][0]["content"] = "private_sentinel"
    else:
        value[field] = False if field == "capture_incomplete" else "f" * 64
    with pytest.raises(ValidationError) as error:
        CaptureSource.model_validate(value)
    assert "private_sentinel" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["capture_source", "correct_capture_source"])
async def test_http_source_routes_send_trusted_scope_and_validate_envelope(kind):
    fixture = json.loads(FIXTURE.read_text())
    value = fixture["base" if kind == "capture_source" else "second"]
    source = CaptureSource.model_validate(value)
    operation = protocol.OPERATION_ADAPTER.validate_python(
        {
            "kind": kind,
            **(
                {"payload": fixture["correction"]}
                if kind == "correct_capture_source"
                else {}
            ),
        }
    )
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200 if kind == "capture_source" else 201, json=value)

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        result = await client_for(http).capture_operation(
            source.session_id,
            source.session_id,
            operation,
            idempotency_key="source-wire-stable-key",
            practitioner_id=source.recovery_history.recoveries[
                0
            ].acknowledged_by_practitioner_id,
        )
    assert result == source
    assert seen[0].url.path.endswith(
        "/capture-source" + ("/corrections" if kind == "correct_capture_source" else "")
    )
    assert (
        seen[0].headers["X-Capture-Practitioner"]
        == source.recovery_history.recoveries[0].acknowledged_by_practitioner_id
    )
