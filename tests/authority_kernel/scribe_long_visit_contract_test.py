"""Receiver compatibility for larger visits; no live capture or service calls."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services import scribe_capture_protocol as p
from app.services.scribe_capture_continuity import CaptureContinuity
from app.services.scribe_capture_limits import MAX_CAPTURE_PLAINTEXT_BYTES
from app.services.scribe_capture_recovery_contract import CaptureRecoveryRequest
from app.services.scribe_source_start_contract import NativeStartPayload


def capabilities(chunks, budget):
    return p.Capabilities(
        media=p.MediaCapabilities(
            media_type="audio/wav",
            encoding="pcm_s16le",
            channels=1,
            sample_rate_hz=16000,
        ),
        target_chunk_duration_ms=10000,
        max_envelope_bytes=327680,
        max_chunks=chunks,
        remaining_aggregate_plaintext_bytes=budget,
        capture_lease_ttl_seconds=900,
        reservation_window_seconds=900,
        max_reservations_per_batch=90,
        upload_concurrency=2,
    )


@pytest.mark.parametrize("chunks,budget", [(1000, 67108864), (2048, 402653184)])
def test_exact_legacy_and_larger_capabilities(chunks, budget):
    receipt = capabilities(chunks, budget)
    assert p.Capabilities.model_validate_json(receipt.model_dump_json()) == receipt
    assert capabilities(chunks, 0).remaining_aggregate_plaintext_bytes == 0


@pytest.mark.parametrize(
    "chunks,budget",
    [
        (1000, 67108865),
        (2048, 402653185),
        (2049, 0),
        (1001, 0),
        (2048, -1),
    ],
)
def test_unrecognized_or_inconsistent_capabilities_rejected(chunks, budget):
    with pytest.raises(ValidationError):
        capabilities(chunks, budget)


def test_three_hour_reservation_and_completed_track_fit():
    # 16 kHz mono PCM16, ten-second WAV chunks. Budget includes protocol overhead.
    assert 1080 * 327680 < MAX_CAPTURE_PLAINTEXT_BYTES < 500_000_000
    for first in range(0, 1080, 90):
        p.ReservePayload(
            capture_lease_id=uuid4(),
            capture_lease_generation=1,
            channel="microphone",
            first_sequence_number=first,
            count=90,
        )
    completed = p.CompletePayload(
        capture_lease_id=uuid4(),
        capture_lease_generation=12,
        channels=(
            p.ChannelCompletion(channel="microphone", final_sequence_number=1079),
        ),
    )
    assert completed.channels[0].final_sequence_number == 1079


@pytest.mark.parametrize("sequence", [999, 1000, 1079, 2047])
def test_extended_positions_round_trip_all_chunk_contracts(sequence):
    scope = p.AudioScope(
        tenant_id=uuid4(),
        session_id=uuid4(),
        object_id=uuid4(),
        channel="microphone",
        sequence_number=sequence,
    )
    registered = p.RegisterPayload(
        channel=scope.channel,
        sequence_number=sequence,
        object_id=scope.object_id,
        sha256="0" * 64,
        ciphertext_size_bytes=327680,
    )
    assert (
        p.RegisterPayload.model_validate_json(registered.model_dump_json())
        == registered
    )
    p.ChannelCompletion(channel="microphone", final_sequence_number=sequence)
    p.ReservePayload(
        capture_lease_id=uuid4(),
        capture_lease_generation=1,
        channel="microphone",
        first_sequence_number=sequence,
        count=1,
    )


@pytest.mark.parametrize("sequence", [-1, 2048, True, 1.5])
def test_out_of_range_or_noninteger_positions_rejected(sequence):
    with pytest.raises(ValidationError):
        p.ChannelCompletion(channel="microphone", final_sequence_number=sequence)


def test_reservation_end_and_batch_bounds_remain_enforced():
    base = dict(
        capture_lease_id=uuid4(), capture_lease_generation=1, channel="microphone"
    )
    p.ReservePayload(**base, first_sequence_number=2047, count=1)
    for first, count in [(2047, 2), (2000, 90), (0, 91)]:
        with pytest.raises(ValidationError):
            p.ReservePayload(**base, first_sequence_number=first, count=count)


def test_late_native_resume_and_continuity_remain_recoverable():
    payload = NativeStartPayload(
        attempt_id=uuid4(),
        source="ios_microphone",
        owner_proof="0" * 64,
        lease_id=uuid4(),
        lease_receipt_id=uuid4(),
        lease_generation=13,
        capture_authorized_until="2026-09-25T15:15:00Z",
        first_sequence_number=1080,
    )
    assert NativeStartPayload.model_validate_json(payload.model_dump_json()) == payload
    legacy = payload.model_copy(update={"first_sequence_number": 0})
    assert "first_sequence_number" not in legacy.model_dump()
    evidence = CaptureContinuity(
        schema_version="1.0",
        continuity="interrupted_or_unconfirmed",
        missing_audio="unknown",
        starts=(
            {"source_start_id": uuid4(), "first_sequence_number": 0},
            {"source_start_id": uuid4(), "first_sequence_number": 1080},
        ),
    )
    assert evidence.starts[1].first_sequence_number == 1080


def test_recovery_can_finish_at_capacity_but_cannot_resume_beyond_it():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/capture_recovery.json").read_text()
    )
    request = fixture["request"] | {
        "first_sequence_number": 1000,
        "next_sequence_number": 1080,
    }
    assert CaptureRecoveryRequest.model_validate(request).next_sequence_number == 1080
    for boundary in [2048, 2049]:
        with pytest.raises(ValidationError):
            CaptureRecoveryRequest.model_validate(
                request | {"next_sequence_number": boundary}
            )
    finished = CaptureRecoveryRequest.model_validate(
        request | {"next_sequence_number": 2048, "decision": "finish_capture"}
    )
    assert finished.next_sequence_number == 2048
