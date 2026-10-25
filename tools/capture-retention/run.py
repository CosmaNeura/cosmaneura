"""Opt-in actual configured-model comparison; unsaved synthetic objects only."""

import argparse
import asyncio
import contextlib
import copy
import fcntl
import hashlib
import importlib
import io
import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from oracle import evaluate

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures.json")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def bootstrap():
    # Use the repository's isolated bootstrap rather than global conftest, whose
    # session hooks can delete users/truncate databases. Only the model is live.
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not key or not endpoint:
        raise SystemExit("BLOCKED: configured staging model credentials missing")
    if urlsplit(endpoint).hostname != "openaitestvhs.openai.azure.com":
        raise SystemExit("BLOCKED: endpoint differs from scoped staging identity")
    sys.path.insert(0, str(ROOT))
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        import tests.authority_kernel.conftest  # noqa: F401

        os.environ.update(AZURE_OPENAI_API_KEY=key, AZURE_OPENAI_ENDPOINT=endpoint)
        import app.configuration.openai as configured

        importlib.reload(configured)
        from app.services import scribe_capture_notes as notes

        from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
        from app.models.doctor import Doctor
        from app.models.doctor_note_schema import DoctorNoteSchema
        from app.models.note_type_customization import NoteTypeCustomization
    logging.disable(logging.CRITICAL)
    # If another imported module loaded notes before reload, use the freshly
    # configured existing Azure client. Never substitute a model answer.
    notes.openai = configured.openai
    return (
        notes,
        AIVisitTranscript,
        TranscriptEntry,
        Doctor,
        DoctorNoteSchema,
        NoteTypeCustomization,
    )


async def main(args):
    fixture = json.loads(FIXTURES.read_text())
    ids = [case["id"] for case in fixture["cases"]]
    if (
        len(ids) != 10
        or len(set(ids)) != 10
        or ids[:2] != ["R2", "R3"]
        or any(
            not name.startswith(f"RET-{i:02d}-") for i, name in enumerate(ids[2:], 1)
        )
    ):
        raise SystemExit("Refusing incomplete or reordered acceptance fixture matrix")
    if not fixture["synthetic_only"] or any(
        not c["synthetic_only"] for c in fixture["cases"]
    ):
        raise SystemExit("Refusing non-synthetic fixture")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.batch == "primary" and (args.roles != "observed" or args.ros):
        raise SystemExit(
            "Primary comparison retains observed Unknown roles and original schema"
        )
    if args.ros:
        fixture["schema"] = {
            key: fixture["schema"][key]
            for key in (
                "S - SUBJECTIVE",
                "Medications",
                "O - OBJECTIVE",
                "A - ASSESSMENT",
                "P - PLAN",
                "Follow-ups",
                "Return precautions",
            )
        }
        fixture["schema"]["ROS"] = (
            "Only explicit symptom responses, preserving source attribution and uncertainty."
        )
        fixture["schema_scope"] = (
            "Constructed compact eight-field schema with explicit ROS"
        )
    notes, Transcript, Entry, Doctor, Schema, Customization = bootstrap()
    baseline, candidate = fixture["baseline_grounding"], notes.GROUNDING
    underlying = notes.openai
    receipts = []

    class Recorder:
        def with_options(self, **options):
            client = underlying.with_options(**options)

            def create(**kwargs):
                started = time.monotonic_ns()
                result = client.chat.completions.create(**kwargs)
                receipts.append(
                    {
                        "duration_ms": (time.monotonic_ns() - started) / 1e6,
                        "model": result.model,
                        "usage": result.usage.model_dump() if result.usage else None,
                        "finish_reason": result.choices[0].finish_reason
                        if result.choices
                        else None,
                        "request_options": {
                            **options,
                            **{
                                k: kwargs[k]
                                for k in (
                                    "model",
                                    "temperature",
                                    "top_p",
                                    "max_tokens",
                                    "response_format",
                                )
                            },
                        },
                    }
                )
                return result

            return SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            )

    notes.openai = Recorder()
    identity = {
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "HEAD"], cwd=ROOT)
        ).hexdigest(),
        "fixtures_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        "configured_model": notes.DEFAULT_MULTI_MODAL_MODEL,
        "endpoint": "openaitestvhs.openai.azure.com",
        "scope": "actual configured model; synthetic unsaved objects; no product login, audio, or persistence",
        "schema_sha256": digest(fixture["schema"]),
        "customization_sha256": digest(fixture["customization"]),
        "baseline_grounding_sha256": digest(baseline),
        "candidate_grounding_sha256": digest(candidate),
        "role_variant": args.roles,
        "ros_variant": args.ros,
        "input_code_sha256": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "app/services/scribe_capture_notes.py",
                "app/services/scribe_capture_plan_grounding.py",
                "app/jobs/summarize.py",
                "app/configuration/openai.py",
                "tools/capture-retention/run.py",
                "tools/capture-retention/oracle.py",
                "uv.lock",
            )
        },
    }
    input_path = output / "inputs.json"
    inputs = {
        "identity": identity,
        "fixture": fixture,
        "candidate_grounding": candidate,
    }
    if input_path.exists():
        if json.loads(input_path.read_text()) != inputs:
            raise SystemExit("Cannot resume with different inputs")
    else:
        write(input_path, inputs)
    results = []
    cases = fixture["cases"][:2] if args.batch == "primary" else fixture["cases"][2:]
    for case in cases:
        case = copy.deepcopy(case)
        if args.roles == "known":
            # These are separate constructed role-controlled edge cases, never
            # relabeling the observed R2/R3 recordings or unknown-role evidence.
            for index, entry in enumerate(case["entries"]):
                entry["speaker"] = (
                    "Doctor"
                    if "question" in case["checks"]
                    or ("medications" in case["checks"] and index == 1)
                    else "Patient"
                )
            if "pain" in case["checks"]:
                case["entries"].append(
                    {
                        "speaker": "Doctor",
                        "content": "My own headache is severe today; that is my history, not yours.",
                    }
                )
                case["checks"].append("known_role")
        for variant in (
            ["baseline", "candidate"] if args.batch == "primary" else ["candidate"]
        ):
            for repetition in range(1, (3 if args.batch == "primary" else 1) + 1):
                name = f"{case['id']}-{variant}-{repetition}"
                receipt_path = output / (name + ".json")
                attempt_path = output / (name + ".attempt.json")
                if receipt_path.exists():
                    prior = json.loads(receipt_path.read_text())
                    if prior["identity"] != identity:
                        raise SystemExit("Cannot resume with different source/fixtures")
                    results.append(prior)
                    continue
                if attempt_path.exists():
                    raise SystemExit(
                        "BLOCKED: prior model attempt has no result; preserve it and use a new run"
                    )
                entries = [
                    Entry(**e, startTime=i * 1000, endTime=(i + 1) * 1000)
                    for i, e in enumerate(case["entries"])
                ]
                schema = Schema(
                    note_type="Synthetic Telehealth SOAP", fields=fixture["schema"]
                )
                customization = Customization(**fixture["customization"])
                doctor = Doctor(biography="")
                transcript = Transcript(duration=len(entries), transcript_data=entries)
                grounding = notes.plans.PlanGrounding(
                    notes.plans.prospective_fields(schema.fields),
                    tuple(
                        notes.plans.PlanSource.speech(e.speaker, e.content)
                        for e in entries
                    ),
                )
                prompt = (
                    notes.summarize.build_structured_prompt(
                        transcript,
                        None,
                        schema,
                        customization,
                        action_items="",
                        doctor=doctor,
                        source_indexes=bool(grounding.fields),
                    )
                    + (
                        notes.capture_retention_context(
                            entries, customization, field_names=tuple(schema.fields)
                        )
                        if variant == "candidate"
                        else ""
                    )
                    + grounding.prompt()
                )
                notes.GROUNDING = baseline if variant == "baseline" else candidate
                item = {
                    "name": name,
                    "identity": identity,
                    "at": datetime.now(UTC).isoformat(),
                    "source_sha256": digest(case["entries"]),
                    "source_entries": case["entries"],
                    "semantic_checks": case["checks"],
                    "prompt_sha256": digest(prompt),
                    "status": "FAIL",
                }
                started = time.monotonic_ns()
                before = len(receipts)
                write(
                    attempt_path,
                    {
                        "name": name,
                        "identity": identity,
                        "at": item["at"],
                        "source_sha256": item["source_sha256"],
                        "prompt_sha256": item["prompt_sha256"],
                        "source_entries": case["entries"],
                        "semantic_checks": case["checks"],
                    },
                )
                try:
                    response_schema = (
                        notes.capture_response_schema(
                            tuple(schema.fields), grounding.fields
                        )
                        if variant == "candidate"
                        else None
                    )
                    raw = await notes.ConfiguredNoteModel(
                        response_schema=response_schema
                    ).complete(prompt, json_output=True)
                    item["raw_response"] = raw
                    raw_fields, plans = grounding.resolve(
                        raw, max_bytes=notes.MAX_MODEL_BYTES
                    )
                    fields = notes._validated_note(raw_fields, schema.fields)
                    fields.update(plans)
                    fields = notes._validated_note(json.dumps(fields), schema.fields)
                    item["resolved_fields"] = fields
                    item["oracle"] = evaluate(case, fields)
                    item["status"] = "PASS" if item["oracle"]["passed"] else "FAIL"
                except Exception as error:
                    # No exception text: SDK errors can include credentials/URLs.
                    item["error_type"] = type(error).__name__
                    item["status_code"] = getattr(error, "status_code", None)
                item["duration_ms"] = (time.monotonic_ns() - started) / 1e6
                item["model_receipts"] = receipts[before:]
                write(receipt_path, item)
                results.append(item)
                print(
                    json.dumps(
                        {
                            "name": name,
                            "status": item["status"],
                            "error_type": item.get("error_type"),
                            "failures": item.get("oracle", {}).get("failures", []),
                            "duration_ms": item["duration_ms"],
                        }
                    ),
                    flush=True,
                )
                if item.get("status_code") in (401, 403, 404):
                    raise SystemExit(
                        "BLOCKED: model access/configuration; receipt preserved"
                    )
                await asyncio.sleep(args.interval)
    expected_count = 12 if args.batch == "primary" else 8
    if (
        len(results) != expected_count
        or len({r["name"] for r in results}) != expected_count
    ):
        raise SystemExit(
            "Incomplete acceptance result matrix; preserved receipts remain unqualified"
        )
    summary = {
        "identity": identity,
        "results": [
            {
                k: r.get(k)
                for k in ("name", "status", "duration_ms", "oracle", "error_type")
            }
            for r in results
        ],
        "candidate_oracle_passed": all(
            r["status"] == "PASS" for r in results if "-candidate-" in r["name"]
        ),
        "all_model_contracts_passed": all("resolved_fields" in r for r in results),
        "independent_content_review_required": True,
    }
    summary_path = output / f"{args.batch}-summary.json"
    if summary_path.exists():
        if json.loads(summary_path.read_text()) != summary:
            raise SystemExit("Prior summary differs; preserve it for review")
    else:
        write(summary_path, summary)
    if (
        not summary["candidate_oracle_passed"]
        or not summary["all_model_contracts_passed"]
    ):
        raise SystemExit("FAIL: one or more candidate model/semantic checks failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--batch", choices=["primary", "edges"], default="primary")
    parser.add_argument("--interval", type=float, default=61)
    parser.add_argument("--roles", choices=["observed", "known"], default="observed")
    parser.add_argument("--ros", action="store_true")
    arguments = parser.parse_args()
    directory = Path(arguments.output).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another evaluator owns this run") from None
        asyncio.run(main(arguments))
