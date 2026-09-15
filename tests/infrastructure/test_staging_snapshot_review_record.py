"""Invented public review fixtures only; no provider/network/consumer calls."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "scripts/staging_snapshot_review_record.py"
SPEC = importlib.util.spec_from_file_location("snapshot_review_record_fixture", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

NOW = datetime(2026, 9, 15, 3, tzinfo=UTC)
TEXT = "# Verdict: **PASS**\n\n- **P0:** None.\n- **P1:** None.\n- **P2:** None.\nSource only."
ID = "resp_0123456789abcdef"
BUNDLE = "a" * 64


def encoded(value: object) -> bytes:
    return module.canonical(value)


def event(kind: str, sequence: int, **fields: object) -> bytes:
    return (
        b"event: "
        + kind.encode()
        + b"\ndata: "
        + encoded({"type": kind, "sequence_number": sequence, **fields})
        + b"\n\n"
    )


def fixtures(kind: str = "source", text: str = TEXT) -> dict[str, bytes]:
    metadata = {
        "review_kind": kind,
        "subject_sha256": BUNDLE,
        "source_bundle_sha256": BUNDLE,
    }
    request = {
        "model": "codex-auto-review",
        "stream": True,
        "background": False,
        "metadata": metadata,
    }
    response = {
        "id": ID,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "metadata": metadata,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
    }
    stream = event("response.created", 0, response={"id": ID, "status": "in_progress"})
    stream += event("response.output_text.delta", 1, delta=text)
    stream += event("response.completed", 2, response=response)
    originals = {
        "request": encoded(request) + b"\n",
        "response": encoded(response) + b"\n",
        "text": text.encode() + b"\n",
        "stream": stream,
        "identity": encoded({"response_id": ID}),
    }
    originals["summary"] = encoded(
        {
            "completed_at": NOW.isoformat(),
            "review_completed": True,
            "response_id": ID,
            "apply_authorized": False,
            "deployment_authorized": False,
            "live_acceptance_attested": False,
            "background_requested": False,
            "storage_option_changed": False,
            "source_review_only": kind == "source",
            "foreground_stream_requested": True,
            "response_json_serialized_from_terminal_stream_event": True,
            "request_sha256": module.sha(originals["request"][:-1]),
            **{
                key + "_sha256": module.sha(originals[key])
                for key in ("response", "text", "stream")
            },
        }
    )
    originals["diagnostic"] = encoded(
        {
            "response_id": ID,
            "failure": None,
            "http_status": 200,
            "terminal_response_captured": True,
            "last_lifecycle_event": "response.completed",
            "stream_bytes": len(stream),
            "stream_sha256": module.sha(stream),
        }
    )
    return originals


def build(originals: dict[str, bytes], **kwargs: object):
    values = {
        "kind": "source",
        "subject_sha256": BUNDLE,
        "source_bundle_sha256": BUNDLE,
        "response_id": ID,
        "completed_at": NOW.isoformat(),
        "now": NOW,
    }
    values.update(kwargs)
    return module.build_review_record(
        originals, {k: module.sha(v) for k, v in originals.items()}, **values
    )


def replace_record(originals: dict[str, bytes], name: str, **fields: object) -> None:
    value = json.loads(originals[name])
    value.update(fields)
    originals[name] = encoded(value)


class ReviewRecordTests(unittest.TestCase):
    def test_original_text_framing_and_stream_limits(self):
        for raw in (TEXT.encode(), TEXT.encode() + b"\n\n", b"replacement\n"):
            with self.subTest(raw=raw[-20:]):
                originals = fixtures()
                originals["text"] = raw
                summary = json.loads(originals["summary"])
                summary["text_sha256"] = module.sha(raw)
                originals["summary"] = encoded(summary)
                with self.assertRaises(module.ReviewRecordRefused):
                    build(originals)
        for raw in (b"", b"x" * (module.LIMITS["stream"] + 1), "not bytes"):
            with self.assertRaises(module.ReviewRecordRefused):
                module.completed_stream(raw, ID)

    def test_invalid_provider_unicode_refuses_without_escaping_text(self):
        response = json.loads(fixtures()["response"])
        response["output"][1]["content"][0]["text"] += "\ud800"
        with self.assertRaises(module.ReviewRecordRefused):
            module.review_text(response)

    def test_exact_text_normalization_preserves_originals_and_false_authority(self):
        originals = fixtures()
        before = copy.deepcopy(originals)
        result = build(originals)
        self.assertEqual(originals, before)
        self.assertEqual(result.text, TEXT.encode())
        self.assertEqual(result.request, originals["request"][:-1])
        self.assertEqual(result.response, originals["response"])
        summary = json.loads(result.summary)
        self.assertEqual(summary["completed_at"], "2026-09-15T03:00:00Z")
        self.assertEqual(summary["findings"], {"p0": [], "p1": [], "p2": []})
        self.assertIs(summary["execution_authorized"], False)
        self.assertIs(summary["deployment_authorized"], False)
        self.assertEqual(summary["text_sha256"], module.sha(TEXT.encode()))
        self.assertNotEqual(summary["text_sha256"], module.sha(originals["text"]))
        self.assertEqual(
            json.loads(result.original_evidence_sha256)["text"],
            module.sha(originals["text"]),
        )

    def test_provider_trailing_newline_is_not_stripped(self):
        for ending in ("\n", "\n\n"):
            with self.subTest(ending=repr(ending)):
                self.assertEqual(
                    build(fixtures(text=TEXT + ending)).text, (TEXT + ending).encode()
                )

    def test_plan_and_handoff_are_only_normalized_not_created(self):
        for kind in ("plan", "handoff"):
            with self.subTest(kind=kind):
                result = build(fixtures(kind), kind=kind)
                self.assertEqual(json.loads(result.summary)["kind"], kind)

    def test_external_hashes_and_closed_catalog(self):
        originals = fixtures()
        expected = {k: module.sha(v) for k, v in originals.items()}
        expected["text"] = "b" * 64
        with self.assertRaises(module.ReviewRecordRefused):
            module.build_review_record(
                originals,
                expected,
                kind="source",
                subject_sha256=BUNDLE,
                source_bundle_sha256=BUNDLE,
                response_id=ID,
                completed_at=NOW.isoformat(),
                now=NOW,
            )
        for name in originals:
            with self.subTest(name=name):
                wrong = dict(originals)
                wrong.pop(name)
                with self.assertRaises(module.ReviewRecordRefused):
                    build(wrong)
        with self.assertRaises(module.ReviewRecordRefused):
            build(originals | {"extra": b"{}"})

    def test_bounds_types_and_duplicate_json_keys(self):
        for key, limit in module.LIMITS.items():
            for raw in (b"", b"x" * (limit + 1)):
                with self.subTest(key=key, size=len(raw)):
                    with self.assertRaises(module.ReviewRecordRefused):
                        build(fixtures() | {key: raw})
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", b"{", b"\xff"):
            with self.subTest(raw=raw):
                with self.assertRaises(module.ReviewRecordRefused):
                    module.record(raw)

    def test_subject_time_and_response_identity(self):
        for override in (
            {"kind": "other"},
            {"kind": []},
            {"subject_sha256": "b" * 64},
            {"source_bundle_sha256": "bad"},
            {"response_id": "resp_other"},
            {"now": NOW - timedelta(seconds=1)},
            {"now": NOW.replace(tzinfo=None)},
            {"completed_at": "invalid"},
            {"completed_at": "2026-09-15T04:00:00+01:00"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(module.ReviewRecordRefused):
                    build(fixtures(), **override)

    def test_original_summary_flags_and_hashes(self):
        for field, wrong in {
            "review_completed": False,
            "response_id": "resp_abcd",
            "apply_authorized": True,
            "deployment_authorized": True,
            "live_acceptance_attested": True,
            "background_requested": True,
            "storage_option_changed": True,
            "source_review_only": False,
            "foreground_stream_requested": False,
            "response_json_serialized_from_terminal_stream_event": False,
            "request_sha256": "b" * 64,
            "response_sha256": "b" * 64,
            "text_sha256": "b" * 64,
            "stream_sha256": "b" * 64,
        }.items():
            with self.subTest(field=field):
                originals = fixtures()
                replace_record(originals, "summary", **{field: wrong})
                with self.assertRaises(module.ReviewRecordRefused):
                    build(originals)

    def test_wrong_request_and_echoed_metadata(self):
        for field, wrong in {
            "model": "other",
            "stream": False,
            "background": True,
            "store": True,
            "metadata": {"review_kind": "source"},
        }.items():
            with self.subTest(field=field):
                originals = fixtures()
                replace_record(originals, "request", **{field: wrong})
                originals["request"] += b"\n"
                with self.assertRaises(module.ReviewRecordRefused):
                    build(originals)
        originals = fixtures()
        replace_record(originals, "response", metadata={"review_kind": "handoff"})
        with self.assertRaises(module.ReviewRecordRefused):
            build(originals)

    def test_uncompleted_or_nonassistant_outputs(self):
        response = json.loads(fixtures()["response"])
        for change in (
            {"status": "incomplete"},
            {"error": {"code": "x"}},
            {"incomplete_details": {"reason": "x"}},
            {"output": []},
            {"output": [{"type": "function_call"}]},
            {
                "output": [
                    {
                        "type": "message",
                        "role": "user",
                        "status": "completed",
                        "content": [],
                    }
                ]
            },
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "in_progress",
                        "content": [],
                    }
                ]
            },
        ):
            with self.subTest(change=change):
                with self.assertRaises(module.ReviewRecordRefused):
                    module.review_text(response | change)

    def test_quoted_duplicate_or_conflicting_decisions(self):
        for text in (
            "```\n" + TEXT + "\n```",
            "> " + TEXT,
            TEXT.replace("**PASS**", "**FAIL**"),
            TEXT + "\n# Verdict: **FAIL**",
            TEXT + "\n- **P1:** A defect.",
            TEXT.replace("- **P0:** None.", "- **P0:** None.\n- **P0:** None."),
        ):
            with self.subTest(text=text[:40]):
                with self.assertRaises(module.ReviewRecordRefused):
                    build(fixtures(text=text))

    def test_sse_lifecycle_sequence_and_tail(self):
        good = fixtures()["stream"]
        self.assertEqual(
            module.completed_stream(good.replace(b"\n", b"\r\n"), ID)["id"], ID
        )
        variants = [
            good[:-1],
            good.replace(b'"sequence_number":1', b'"sequence_number":0'),
            good.replace(b'"sequence_number":1', b'"sequence_number":true'),
            good.replace(b'"response.created"', b'"response.in_progress"'),
            good
            + event(
                "response.completed", 3, response={"id": ID, "status": "completed"}
            ),
            good + b"garbage",
            good.replace(b'"resp_0123456789abcdef"', b'"resp_0000"', 1),
            event("error", 0),
            b"data: {}\n\n",
            b"event: x\nevent: x\ndata: {}\n\n",
            good.replace(b"event: response.created", b"event: response.failed"),
            event("response.completed", 0, response={"id": ID, "status": "completed"}),
        ]
        for raw in variants:
            with self.subTest(raw=raw[:50]):
                with self.assertRaises(module.ReviewRecordRefused):
                    module.completed_stream(raw, ID)

    def test_stream_response_and_diagnostics_must_match(self):
        for field, wrong in {
            "response_id": "resp_abcd",
            "failure": "timeout",
            "http_status": 500,
            "terminal_response_captured": False,
            "last_lifecycle_event": "response.failed",
            "stream_bytes": True,
            "stream_sha256": "b" * 64,
        }.items():
            with self.subTest(field=field):
                originals = fixtures()
                replace_record(originals, "diagnostic", **{field: wrong})
                with self.assertRaises(module.ReviewRecordRefused):
                    build(originals)
        for name in ("response", "identity"):
            originals = fixtures()
            replace_record(originals, name, id="resp_abcd", response_id="resp_abcd")
            with self.assertRaises(module.ReviewRecordRefused):
                build(originals)


if __name__ == "__main__":
    unittest.main()
