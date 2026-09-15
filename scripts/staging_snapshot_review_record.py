"""Normalize retained public review evidence, never authenticate or authorize it.

The caller/reviewer must obtain external hashes and response identity from the
actual authenticated, authorized transport. Self-authored inputs are NOT proof.
This pure adapter performs no network, file writes, source loading, human action,
plan/handoff generation or renewal. Preserve original transport files unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

ENDPOINT = "https://cosmascode.openai.azure.com/openai/v1/responses"
POLICY = "cosmaneura-staging-human-snapshot-v1"
LIMITS = {
    "request": 500001,  # Original protected sender file has one framing newline.
    "response": 2 * 1024**2,
    "text": 128 * 1024 + 1,
    "summary": 16384,
    "stream": 8 * 1024**2,
    "identity": 16384,
    "diagnostic": 16384,
}


class ReviewRecordRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise ReviewRecordRefused("snapshot_review_record_refused")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def valid_sha(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def invalid_constant(_value: str) -> None:
    raise ReviewRecordRefused("snapshot_review_record_refused")


def record(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw, object_pairs_hook=object_pairs, parse_constant=invalid_constant
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ReviewRecordRefused("snapshot_review_record_refused") from None
    require(type(value) is dict)
    return value


def timestamp(value: object) -> datetime:
    require(type(value) is str and len(value) <= 40)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise ReviewRecordRefused("snapshot_review_record_refused") from None
    require(parsed.tzinfo is UTC)
    return parsed


def completed_stream(raw: bytes, response_id: str) -> dict[str, Any]:
    """Match the bounded recorded SSE lifecycle, including all captured tail bytes."""
    require(type(raw) is bytes and 0 < len(raw) <= LIMITS["stream"])
    normalized = raw.replace(b"\r\n", b"\n")
    require(b"\r" not in normalized and normalized.endswith(b"\n\n"))
    events = normalized.split(b"\n\n")
    require(len(events) <= 50002)
    sequence, created, terminal = -1, False, None
    for frame in events:
        if not frame:
            continue
        require(terminal is None and len(frame) <= 2 * 1024**2)
        data, name = [], None
        for line in frame.split(b"\n"):
            if line.startswith(b":"):
                continue
            field, colon, value = line.partition(b":")
            require(colon and field in {b"event", b"data"})
            value = value.removeprefix(b" ")
            if field == b"event":
                require(name is None)
                name = value
            else:
                data.append(value)
        if not data:
            continue
        event = record(b"\n".join(data))
        kind = event.get("type")
        require(type(kind) is str and re.fullmatch(r"[a-z_.]{1,100}", kind))
        kind = cast(str, kind)
        require(name is None or name == kind.encode())
        current = event.get("sequence_number")
        # Provider frames are numbered from zero without gaps. A monotonic but
        # incomplete capture is not complete evidence, even if its hashes match.
        require(type(current) is int and current == sequence + 1)
        sequence = cast(int, current)
        require(kind not in {"error", "response.failed", "response.incomplete"})
        if kind in {"response.created", "response.in_progress", "response.completed"}:
            value = event.get("response")
            require(type(value) is dict and value.get("id") == response_id)
            value = cast(dict[str, Any], value)
            if kind == "response.created":
                require(not created)
                created = True
            else:
                require(created)
            expected_status = (
                "completed" if kind == "response.completed" else "in_progress"
            )
            require(value.get("status") == expected_status)
            if kind == "response.completed":
                terminal = value
        else:
            require(created)
    require(created and type(terminal) is dict)
    return cast(dict[str, Any], terminal)


def review_text(response: dict[str, Any]) -> bytes:
    require(response.get("status") == "completed")
    require(
        response.get("error") is None and response.get("incomplete_details") is None
    )
    require(type(response.get("output")) is list)
    parts = []
    for item in response["output"]:
        require(type(item) is dict and item.get("type") in {"message", "reasoning"})
        if item["type"] == "message":
            require(
                item.get("role") == "assistant" and item.get("status") == "completed"
            )
            require(type(item.get("content")) is list)
            for part in cast(list[Any], item["content"]):
                require(type(part) is dict and part.get("type") == "output_text")
                require(type(part.get("text")) is str)
                parts.append(part["text"])
    require(parts)
    try:
        raw = "\n".join(parts).encode()
    except UnicodeError:
        raise ReviewRecordRefused("snapshot_review_record_refused") from None
    require(0 < len(raw) <= 128 * 1024)
    lines = raw.decode().splitlines()
    require(lines[0] == "# Verdict: **PASS**")
    decisions = [
        line
        for line in lines
        if line.startswith("# Verdict:") or re.match(r"^- \*\*P[012]:", line)
    ]
    require(
        decisions
        == [
            "# Verdict: **PASS**",
            "- **P0:** None.",
            "- **P1:** None.",
            "- **P2:** None.",
        ]
    )
    return raw


@dataclass(frozen=True)
class ReviewRecord:
    request: bytes
    response: bytes
    text: bytes
    summary: bytes
    original_evidence_sha256: bytes


def build_review_record(
    originals: dict[str, bytes],
    external_hashes: dict[str, str],
    *,
    kind: str,
    subject_sha256: str,
    source_bundle_sha256: str,
    response_id: str,
    completed_at: str,
    now: datetime,
) -> ReviewRecord:
    """External anchors require independent transport provenance, not this function."""
    require(type(originals) is dict and set(originals) == set(LIMITS))
    require(type(external_hashes) is dict and set(external_hashes) == set(LIMITS))
    for name, limit in LIMITS.items():
        raw = originals[name]
        require(type(raw) is bytes and 0 < len(raw) <= limit)
        require(valid_sha(external_hashes[name]) and sha(raw) == external_hashes[name])
    require(type(kind) is str and kind in {"source", "plan", "handoff"})
    require(valid_sha(subject_sha256) and valid_sha(source_bundle_sha256))
    require(kind != "source" or subject_sha256 == source_bundle_sha256)
    require(type(response_id) is str and re.fullmatch(r"resp_[0-9a-f]+", response_id))
    require(type(now) is datetime and now.tzinfo is UTC)
    completed = timestamp(completed_at)
    require(completed <= now)
    sender = record(originals["summary"])
    require(timestamp(sender.get("completed_at")) == completed)
    require(
        sender.get("review_completed") is True
        and sender.get("response_id") == response_id
    )
    for key in (
        "apply_authorized",
        "deployment_authorized",
        "live_acceptance_attested",
        "background_requested",
        "storage_option_changed",
    ):
        require(sender.get(key) is False)
    require(sender.get("source_review_only") is (kind == "source"))
    require(sender.get("foreground_stream_requested") is True)
    require(sender.get("response_json_serialized_from_terminal_stream_event") is True)
    request_file = originals["request"]
    require(request_file.endswith(b"\n"))
    request_raw = request_file[:-1]
    request = record(request_raw)
    require(request.get("model") == "codex-auto-review")
    require(request.get("stream") is True and request.get("background") is False)
    require("store" not in request)
    metadata = request.get("metadata")
    require(type(metadata) is dict)
    metadata = cast(dict[str, Any], metadata)
    for key, expected in {
        "review_kind": kind,
        "subject_sha256": subject_sha256,
        "source_bundle_sha256": source_bundle_sha256,
    }.items():
        require(metadata.get(key) == expected)
    response = record(originals["response"])
    require(response.get("id") == response_id)
    require(canonical(response.get("metadata")) == canonical(metadata))
    require(
        canonical(completed_stream(originals["stream"], response_id))
        == canonical(response)
    )
    require(record(originals["identity"]).get("response_id") == response_id)
    diagnostic = record(originals["diagnostic"])
    require(
        diagnostic.get("response_id") == response_id
        and diagnostic.get("failure") is None
    )
    require(
        diagnostic.get("http_status") == 200
        and diagnostic.get("terminal_response_captured") is True
    )
    require(diagnostic.get("last_lifecycle_event") == "response.completed")
    require(
        type(diagnostic.get("stream_bytes")) is int
        and diagnostic["stream_bytes"] == len(originals["stream"])
    )
    require(diagnostic.get("stream_sha256") == sha(originals["stream"]))
    for key, raw in {
        "request_sha256": request_raw,
        "response_sha256": originals["response"],
        "text_sha256": originals["text"],
        "stream_sha256": originals["stream"],
    }.items():
        require(sender.get(key) == sha(raw))
    text = review_text(response)
    # The sender appends exactly one framing newline, not part of provider text.
    # Preserve that original file and return separately normalized exact text.
    require(originals["text"] == text + b"\n")
    summary = {
        "schema_version": 1,
        "policy_id": POLICY,
        "kind": kind,
        "subject_sha256": subject_sha256,
        "source_bundle_sha256": source_bundle_sha256,
        "provider": ENDPOINT,
        "request_sha256": sha(request_raw),
        "response_sha256": sha(originals["response"]),
        "text_sha256": sha(text),
        "response_id": response_id,
        "completed_at": completed.isoformat().replace("+00:00", "Z"),
        "verdict": "PASS",
        "findings": {"p0": [], "p1": [], "p2": []},
        "execution_authorized": False,
        "deployment_authorized": False,
    }
    return ReviewRecord(
        request_raw,
        originals["response"],
        text,
        canonical(summary),
        canonical(external_hashes),
    )


if __name__ == "__main__":
    raise SystemExit("snapshot_review_record_requires_independent_transport_evidence")
