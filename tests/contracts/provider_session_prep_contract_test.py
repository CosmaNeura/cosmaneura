"""Pins the provider Session Prep v1 wire contract and synthetic fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "schemas" / "provider-session-prep-v1"
FIXTURE_DIR = ROOT / "contracts" / "fixtures" / "provider-session-prep-v1"

EXPECTED_HASHES = {
    SCHEMA_DIR / "request.schema.json": (
        "657a75acbe2cf4fb05019c1764f829e88a499b50fae61b6746d19ab5c79ff9f7"
    ),
    SCHEMA_DIR / "result.schema.json": (
        "5e109bc901276dfe784ded2212fcc0c877f64ed60e1afd1b351046330138b17b"
    ),
    FIXTURE_DIR / "request-brief.json": (
        "4edc9bdc767202d19d4f435b4d57e66ecbed4988812ac621c06bef98cc6ed832"
    ),
    FIXTURE_DIR / "request-supporting-context.json": (
        "b4896560f9c27ed34373fb6b11cf6c68224f5f43c3a871cc10da3ce3431e1475"
    ),
    FIXTURE_DIR / "result-pending.json": (
        "a3be0bd8ef517e4573479e81c93e67e0f71258b72b50c5a7801f9d09da507756"
    ),
    FIXTURE_DIR / "result-ready-brief.json": (
        "fc14f4d8a020809aa45db7e4b5bfb00a02c6319babf4003dd4336ec06b79491a"
    ),
    FIXTURE_DIR / "result-ready-supporting-context.json": (
        "7242063179b40c0c2923fc768ed4dac2337aefa3a0feaa18320365c9fb557a82"
    ),
    FIXTURE_DIR / "result-refreshing-brief.json": (
        "e8a5fb225f0fe75d71e453df99a543a33120450536fd4db86ba7bb474f0af155"
    ),
    FIXTURE_DIR / "result-unavailable.json": (
        "cd34858a808f1dd28825a129bb4655e77d10adc14a956a7cad2774bfbf81ff02"
    ),
}

RESULT_FIXTURES = {
    path.stem.removeprefix("result-"): path
    for path in FIXTURE_DIR.glob("result-*.json")
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _brief_source_refs(value: object) -> set[str]:
    if isinstance(value, dict):
        refs = set(value.get("source_refs", []))
        for child in value.values():
            refs.update(_brief_source_refs(child))
        return refs
    if isinstance(value, list):
        refs: set[str] = set()
        for child in value:
            refs.update(_brief_source_refs(child))
        return refs
    return set()


def test_request_contract_is_closed() -> None:
    schema = _load(SCHEMA_DIR / "request.schema.json")
    properties = schema["properties"]
    arguments = properties["arguments"]

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "tool_name",
        "tool_version",
        "subject_ref",
        "arguments",
    }
    assert properties["tool_name"] == {"const": "session-prep.context.read"}
    assert properties["tool_version"] == {"const": "1.0.0"}
    assert properties["subject_ref"] == {"type": "null"}
    assert arguments["additionalProperties"] is False
    assert set(arguments["required"]) == {
        "source_turn_ref",
        "context_snapshot_ref",
        "purpose",
        "detail",
    }


def test_result_fixtures_pin_stable_states_and_content_relationships() -> None:
    schema = _load(SCHEMA_DIR / "result.schema.json")
    required = set(schema["required"])
    fixtures = {name: _load(path) for name, path in RESULT_FIXTURES.items()}

    assert schema["additionalProperties"] is False
    assert set(fixtures) == {
        "ready-brief",
        "ready-supporting-context",
        "refreshing-brief",
        "pending",
        "unavailable",
    }
    assert {fixture["state"] for fixture in fixtures.values()} == {
        "ready",
        "refreshing",
        "pending",
        "unavailable",
    }

    for fixture in fixtures.values():
        assert set(fixture) == required
        assert fixture["schema_version"] == "1.0.0"
        included_refs = {
            item["source_ref"] for item in fixture["provenance"] if item["included"]
        }
        assert fixture["source_count"] == len(included_refs)

        if fixture["state"] in {"ready", "refreshing"}:
            assert fixture["prepared_brief"] is not None
            assert _brief_source_refs(fixture["prepared_brief"]) <= included_refs
            if fixture["detail"] == "brief":
                assert fixture["supporting_context"] is None
            else:
                assert fixture["supporting_context"]
                assert {
                    item["source_ref"] for item in fixture["supporting_context"]
                } <= included_refs
        else:
            assert fixture["freshness"] == "none"
            assert fixture["completeness"] == "none"
            assert fixture["prepared_brief"] is None
            assert fixture["supporting_context"] is None
            assert fixture["provenance"] == []
            assert fixture["source_count"] == 0

    assert fixtures["ready-brief"]["freshness"] == "current"
    assert fixtures["ready-brief"]["retry_after_seconds"] is None
    assert fixtures["ready-brief"]["reason_code"] is None
    assert fixtures["refreshing-brief"]["freshness"] == "stale"
    assert fixtures["refreshing-brief"]["reason_code"] == "refresh_in_progress"
    assert fixtures["pending"]["reason_code"] == "generation_pending"
    assert fixtures["unavailable"]["retry_after_seconds"] is None
    assert fixtures["unavailable"]["reason_code"] == "context_not_available"


def test_schema_and_fixtures_are_byte_pinned() -> None:
    for path, expected in EXPECTED_HASHES.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
