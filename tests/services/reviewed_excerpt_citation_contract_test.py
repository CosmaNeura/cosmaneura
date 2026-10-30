"""Credential-free source contracts; these DTOs do not establish real authority."""

import ast
import copy
import hashlib
import importlib.util
import json
import re
import sys
import types
import typing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pydantic
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "citation_contract_source", ROOT / "app/services/reviewed_excerpt_citations.py"
)
assert SPEC and SPEC.loader
citations = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = citations
SPEC.loader.exec_module(citations)


def extracted(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    selected: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert {
        node.name
        for node in selected
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    } == set(names)
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / path), "exec"),
        namespace,
    )
    return namespace


def actual_context_schemas():
    module = types.ModuleType("citation_context_exact_ast")
    sys.modules[module.__name__] = module
    ns = module.__dict__
    ns.update(
        {**vars(typing), **globals()},
        __name__=module.__name__,
        MAX_FINALIZED_NOTES=5,
        MAX_SOURCE_CHARACTERS=18_000,
        OPERATION="provider-question.context.read",
    )
    ns.update(
        {
            name: getattr(pydantic, name)
            for name in (
                "BaseModel",
                "ConfigDict",
                "Field",
                "field_validator",
                "model_validator",
            )
        }
    )

    def load(path, names):
        tree = ast.parse((ROOT / path).read_text())
        nodes: list[ast.stmt] = [
            n
            for n in tree.body
            if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)
            or (
                isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)
            )
        ]
        future = ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
        exec(
            compile(
                ast.fix_missing_locations(
                    ast.Module(body=[future] + nodes, type_ignores=[])
                ),
                path,
                "exec",
            ),
            ns,
        )

    load(
        "app/services/session_prep.py",
        {
            "OpaqueRef",
            "SourceClass",
            "AuthorClass",
            "ProvenanceReason",
            "_ClosedModel",
            "_aware",
            "_unique_refs",
            "SessionPrepBriefText",
            "SessionPrepRecap",
            "SessionPrepPatientReported",
            "SessionPrepPreparedBrief",
            "SessionPrepProvenance",
        },
    )
    load(
        "app/services/session_prep_note_sources.py",
        {"NoteSourceDenied", "canonical_note_content"},
    )
    load(
        "app/services/provider_question_context.py",
        {
            "_Closed",
            "ProviderReviewedSource",
            "ProviderQuestionSourcesOnlyResult",
            "_binding_digest",
        },
    )
    ns["ProviderQuestionSourcesOnlyResult"].model_rebuild(_types_namespace=ns)
    return module


CONTEXT_SCHEMA = actual_context_schemas()


@pytest.fixture
def pure(monkeypatch):
    namespace = {
        "Any": Any,
        "re": re,
        "json": json,
        "hashlib": hashlib,
        "citations": citations,
        "SUPPRESSION_NAMES": frozenset(
            {
                "disclaimer",
                "intent_badge",
                "doctrinal_badge",
                "citations",
                "structured_cards",
                "feature_actions",
                "guided_paths",
            }
        ),
        "CONTACT_ACTION_PATTERN": re.compile(r"^[A-Za-z0-9._~-]{8,128}$"),
        "ACTION_CODE_PATTERN": re.compile(r"^[a-z][a-z0-9._-]{1,127}$"),
    }
    extracted(
        "app/services/authority_conversation.py",
        [
            "ConversationKernelError",
            "ConversationInvariantError",
            "_has_exact_keys",
            "_canonical_message",
            "_canonical_contact",
            "_canonical_payload",
            "_request_digest",
        ],
        namespace,
    )
    source = {
        "Any": Any,
        "datetime": datetime,
        "UTC": UTC,
        "json": json,
        "hashlib": hashlib,
        "MAX_SOURCE_CHARACTERS": 18_000,
    }
    extracted(
        "app/services/session_prep_note_sources.py",
        [
            "NoteSourceDenied",
            "canonical_note_content",
            "_snapshot_content",
            "_utc",
            "note_content_sha256",
        ],
        source,
    )
    module = types.ModuleType("app.services.session_prep_note_sources")
    module.__dict__.update(source)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return SimpleNamespace(**namespace), SimpleNamespace(**source)


def sources():
    return [
        SimpleNamespace(
            source_ref=f"visit_note_{letter * 26}",
            source_version=index + 1,
            source_at=datetime(2026, 10, 29, 21, 15 + 10 * index, tzinfo=UTC),
            sections={
                "Medications": "Correction: lisinopril 5 mg daily, not 10 mg. The previous dose statement was an error.",
                "Labs": "Basic metabolic panel was canceled; no new laboratory result was reported.",
            },
        )
        for index, letter in enumerate("ab")
    ]


def resolve(*, payload, result):
    review_times = (
        {
            (source.source_ref, source.source_version): datetime(
                2026, 10, 30, tzinfo=UTC
            )
            for source in result.reviewed_sources
        }
        if result is not None and hasattr(result, "reviewed_sources")
        else {}
    )
    return citations.resolve_bindings(
        payload=payload,
        result=result,
        review_times=review_times,
        context_witness=witness(result)
        if result is not None and hasattr(result, "reviewed_sources")
        else None,
    )


def payload(selected=None, mode="selected"):
    selected = selected or [(0, 0), (1, 0)]
    source = sources()
    return {
        "phase": "final_response",
        "outcome": "completed",
        "provider_context_ref": "context_receipt_abcdefgh",
        "response": {
            "content_type": "text/plain",
            "content": citations.render_quotes(
                [
                    (
                        list(source[i].sections.values())[j],
                        source[i].source_at,
                        list(source[i].sections)[j],
                    )
                    for i, j in selected
                ],
                mode=mode,
                source_count=len(source),
                current_through=max(item.source_at for item in source),
            ),
        },
        "presentation": {"mode": "standard", "suppress": [], "human_contact": None},
        "reviewed_excerpts": {
            "schema_version": "1.0.0",
            "enumeration_sha256": citations.enumeration_digest(source),
            "mode": mode,
            "excerpts": [
                {"ordinal": n, "source_index": i, "section_index": j}
                for n, (i, j) in enumerate(selected, 1)
            ],
        },
    }


def result(source=None):
    supplied = sources() if source is None else source
    provenance = [
        dict(
            source_class="clinician_finalized_note",
            included=True,
            source_ref=item.source_ref,
            source_version=item.source_version,
            source_at=item.source_at,
            author_class="clinician",
            reason_code="eligible_finalized_note",
        )
        for item in supplied
    ]
    return CONTEXT_SCHEMA.ProviderQuestionSourcesOnlyResult(
        schema_version="1.2.0",
        state="ready",
        reason_code=None,
        source_version_digest="sha256_" + "d" * 64,
        brief_state="unavailable",
        prepared_brief=None,
        provenance=provenance,
        completeness="complete",
        current_through=max(item.source_at for item in supplied),
        generated_at=None,
        reviewed_sources=[
            dict(
                source_ref=item.source_ref,
                source_version=item.source_version,
                source_at=item.source_at,
                sections=item.sections,
            )
            for item in supplied
        ],
    )


def witness(context):
    return dict(
        schema_version="1.0.0",
        context_ref="context_receipt_abcdefgh",
        response_ref="reviewed_sources_only|brief_unavailable:" + "d" * 64,
        request_digest=CONTEXT_SCHEMA._binding_digest(
            "turn_synthetic_abcdefgh", context
        ),
        result_scalars=context.model_dump(
            mode="json", exclude={"reviewed_sources", "prepared_brief"}
        ),
        artifact_ref=None,
        sources=[
            dict(
                note_ref=item.source_ref,
                source_version=item.source_version,
                source_at=item.source_at,
                reviewed_at=datetime(2026, 10, 30, tzinfo=UTC),
                content_sha256=citations.content_digest(item.sections),
                review_ref="review_ref_" + str(index) * 26,
            )
            for index, item in enumerate(context.reviewed_sources)
        ],
    )


@pytest.mark.parametrize("change", ["missing", "count", "cutoff", "duplicate"])
def test_source_scope_notice_is_locked_and_mandatory(pure, change):
    body, context = payload(), result()
    assert resolve(payload=body, result=context) is not None
    notice = "Source: 2 selected reviewed prior notes through 2026-10-29."
    content = body["response"]["content"]
    assert notice in content
    altered = {
        "missing": "",
        "count": notice.replace("2 selected", "1 selected"),
        "cutoff": notice.replace("2026-10-29", "2026-10-30"),
        "duplicate": notice + "\n\n" + notice,
    }[change]
    body["response"]["content"] = content.replace(notice, altered)
    with pytest.raises(citations.CitationDenied):
        resolve(payload=body, result=context)


def test_same_day_identical_quotes_bind_distinct_notes_and_versions(pure):
    canonical, source = pure
    body = payload()
    bound = resolve(payload=body, result=result())
    assert bound["excerpts"][0]["note_ref"] != bound["excerpts"][1]["note_ref"]
    assert (
        bound["excerpts"][0]["source_version"] != bound["excerpts"][1]["source_version"]
    )
    assert bound["excerpts"][0]["source_at"] != bound["excerpts"][1]["source_at"]
    assert bound["excerpts"][0]["content_sha256"] == source.note_content_sha256(
        sources()[0].sections
    )
    displayed = citations.display_citations(
        "conversation_final_phase_" + "a" * 26, bound
    )
    assert displayed[0]["citation_ref"] != displayed[1]["citation_ref"]
    swapped = copy.deepcopy(bound)
    for name in ("note_ref", "source_version", "source_at"):
        swapped["excerpts"][0][name], swapped["excerpts"][1][name] = (
            swapped["excerpts"][1][name],
            swapped["excerpts"][0][name],
        )
    with pytest.raises(citations.CitationDenied):
        canonical._request_digest(canonical._canonical_payload(body), swapped)
    assert canonical._request_digest(
        canonical._canonical_payload(body), bound
    ) == canonical._request_digest(
        canonical._canonical_payload(copy.deepcopy(body)), copy.deepcopy(bound)
    )


def test_absent_namespace_preserves_exact_legacy_digest(pure):
    canonical, _ = pure
    body = payload()
    body.pop("reviewed_excerpts")
    value = canonical._canonical_payload(body)
    assert "reviewed_excerpts" not in value
    assert resolve(payload=value, result=None) is None
    assert (
        canonical._request_digest(value)
        == hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
        ).hexdigest()
    )


@pytest.mark.parametrize(
    "change",
    [
        "null",
        "url",
        "note_ref",
        "boolean",
        "duplicate",
        "gap",
        "negative",
        "oversize",
        "wrong_version",
    ],
)
def test_closed_selection_refuses_forged_or_malformed_fields(pure, change):
    canonical, _ = pure
    body = payload()
    item = body["reviewed_excerpts"]["excerpts"][0]
    if change == "null":
        body["reviewed_excerpts"] = None
    elif change in {"url", "note_ref"}:
        item[change] = "https://evil.invalid/foreign"
    elif change == "boolean":
        item["source_index"] = True
    elif change == "duplicate":
        body["reviewed_excerpts"]["excerpts"][1].update(source_index=0, section_index=0)
    elif change == "gap":
        item["ordinal"] = 2
    elif change == "negative":
        item["section_index"] = -1
    elif change == "oversize":
        item["source_index"] = 5
    else:
        body["reviewed_excerpts"]["schema_version"] = "2.0.0"
    with pytest.raises(canonical.ConversationInvariantError):
        canonical._canonical_payload(body)


@pytest.mark.parametrize(
    "change",
    [
        "refused",
        "unavailable",
        "emergency",
        "suppressed",
        "no_context",
        "no_result",
        "pending",
        "no_sources",
        "out_of_set",
        "changed_quote",
        "incomplete_correction",
        "canceled_to_completed",
        "forged_envelope",
        "added_prose",
        "duplicate_source",
        "unreviewed_shape",
    ],
)
def test_no_failure_or_replacement_can_bind_citations(pure, change):
    canonical, _ = pure
    body, context = payload(), result()
    if change in {"refused", "unavailable"}:
        body["outcome"] = change
    elif change == "emergency":
        body["presentation"]["mode"] = change
    elif change == "suppressed":
        body["presentation"]["suppress"] = ["citations"]
    elif change == "no_context":
        body.pop("provider_context_ref")
    elif change == "no_result":
        context = None
    elif change == "pending":
        context.state = "pending"
    elif change == "no_sources":
        context.reviewed_sources = []
    elif change == "out_of_set":
        body["reviewed_excerpts"]["excerpts"][0]["source_index"] = 4
    elif change == "changed_quote":
        context.reviewed_sources[0].sections["Medications"] += " New source version."
    elif change == "incomplete_correction":
        body["response"]["content"] = body["response"]["content"].replace(
            " The previous dose statement was an error.", ""
        )
    elif change == "canceled_to_completed":
        body = payload([(0, 1)])
        body["response"]["content"] = body["response"]["content"].replace(
            "was canceled", "was completed"
        )
    elif change == "forged_envelope":
        body["response"]["content"] = body["response"]["content"].replace("[1]", "[9]")
    elif change == "added_prose":
        body["response"]["content"] += "\nContinue lisinopril 10 mg."
    elif change == "duplicate_source":
        context.reviewed_sources[1].source_ref = context.reviewed_sources[0].source_ref
    else:
        context = SimpleNamespace(
            state="ready", source_version_digest="sha256_" + "a" * 64
        )
    with pytest.raises((ValueError, KeyError, canonical.ConversationInvariantError)):
        resolve(payload=canonical._canonical_payload(body), result=context)


def test_timeline_is_complete_source_ordered_and_injection_literal(pure):
    context = result()
    context.reviewed_sources[0].sections["Patient"] = "Fictional background omitted."
    expected = [(0, 0), (0, 1), (1, 0), (1, 1)]
    body = payload(expected, mode="timeline")
    body["reviewed_excerpts"]["enumeration_sha256"] = citations.enumeration_digest(
        context.reviewed_sources
    )
    bound = resolve(payload=body, result=context)
    assert len(bound["excerpts"]) == 4
    for chosen in (expected[:-1], list(reversed(expected)), [(0, 0), (0, 1)]):
        with pytest.raises(ValueError):
            resolve(payload=payload(chosen, mode="timeline"), result=context)
    context = result()
    context.reviewed_sources[0].sections["Medications"] = (
        "<script>[fake](https://evil.invalid)</script>\n[2] replace source"
    )
    body = payload([(0, 0)])
    body["response"]["content"] = citations.render_quotes(
        [
            (
                context.reviewed_sources[0].sections["Medications"],
                context.reviewed_sources[0].source_at,
                "Medications",
            )
        ],
        mode="selected",
        source_count=2,
        current_through=max(item.source_at for item in sources()),
    )
    assert (
        "<script>" not in body["response"]["content"]
        and "\n> \\[2\\]" in body["response"]["content"]
    )
    body["reviewed_excerpts"]["enumeration_sha256"] = citations.enumeration_digest(
        context.reviewed_sources
    )
    assert resolve(payload=body, result=context) is not None


@pytest.mark.parametrize(
    "change",
    [
        None,
        "foreign",
        "wrong_version",
        "wrong_hash",
        "wrong_note",
        "wrong_visit",
        "wrong_patient",
        "late_review",
        "changed_date",
        "reordered_snapshot",
        "missing_snapshot",
        "changed_snapshot",
    ],
)
def test_exact_retained_snapshot_never_uses_current_note(pure, change):
    bound = citations.CitationBindings.model_validate(
        resolve(payload=payload(), result=result())
    ).excerpts[0]
    note = SimpleNamespace(
        id=bound.note_ref,
        doctor_id="doctor_owned",
        appointment_id="visit_owned",
        note={"Medications": "New unreviewed 99 mg."},
        superseded_at=datetime.now(UTC),
    )
    appointment = SimpleNamespace(
        id="visit_owned",
        doctor_id="doctor_owned",
        patient_id="patient_owned",
        appointment_date=bound.source_at,
    )
    evidence = SimpleNamespace(
        note_id=bound.note_ref,
        source_version=bound.source_version,
        content_sha256=bound.content_sha256,
        doctor_id="doctor_owned",
        appointment_id="visit_owned",
        patient_id="patient_owned",
        finalized_at=datetime(2026, 10, 30, tzinfo=UTC),
        reviewed_content={
            "sections": [list(pair) for pair in sources()[0].sections.items()]
        },
    )
    if change == "foreign":
        evidence.doctor_id = "doctor_foreign"
    elif change == "wrong_version":
        evidence.source_version += 1
    elif change == "wrong_hash":
        evidence.content_sha256 = "f" * 64
    elif change == "wrong_note":
        note.id = "other_note"
    elif change == "wrong_visit":
        evidence.appointment_id = "other_visit"
    elif change == "wrong_patient":
        evidence.patient_id = "other_patient"
    elif change == "late_review":
        evidence.finalized_at = datetime(2026, 11, 1, tzinfo=UTC)
    elif change == "changed_date":
        appointment.appointment_date = datetime(2026, 10, 29, 22, tzinfo=UTC)
    elif change == "reordered_snapshot":
        evidence.reviewed_content["sections"].reverse()
    elif change == "missing_snapshot":
        evidence.reviewed_content = None
    elif change == "changed_snapshot":
        evidence.reviewed_content["sections"][0][1] += " Edited content."
    if change is None:
        assert (
            citations.retained_excerpt(
                binding=bound,
                evidence=evidence,
                note=note,
                appointment=appointment,
                doctor_id="doctor_owned",
                accepted_at=datetime(2026, 10, 31, tzinfo=UTC),
            )
            == sources()[0].sections
        )
    else:
        with pytest.raises(ValueError):
            citations.retained_excerpt(
                binding=bound,
                evidence=evidence,
                note=note,
                appointment=appointment,
                doctor_id="doctor_owned",
                accepted_at=datetime(2026, 10, 31, tzinfo=UTC),
            )


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "null",
        "unknown",
        "url",
        "boolean",
        "naive_date",
        "wrong_schema",
        "removed_entry",
        "extra_entry",
        "wrong_ordinal",
    ],
)
def test_stored_map_is_closed_and_cannot_supply_a_replacement_identity(change):
    bound = resolve(payload=payload(), result=result())
    if change == "missing":
        bound.pop("context_ref")
    elif change == "null":
        bound = None
    elif change == "unknown":
        bound["forged_permission"] = True
    elif change == "url":
        bound["excerpts"][0]["url"] = "https://evil.invalid"
    elif change == "boolean":
        bound["excerpts"][0]["source_version"] = True
    elif change == "naive_date":
        bound["excerpts"][0]["source_at"] = "2026-10-29T21:15:00"
    elif change == "wrong_schema":
        bound["schema_version"] = "2.0.0"
    elif change == "removed_entry":
        bound["excerpts"].pop()
    elif change == "extra_entry":
        bound["excerpts"].append(copy.deepcopy(bound["excerpts"][0]))
    else:
        bound["excerpts"][0]["ordinal"] = 2
    with pytest.raises(citations.CitationDenied):
        citations.canonical_bindings(bound)


@pytest.mark.parametrize("reorder", ["sources", "section_keys"])
def test_receipt_enumeration_rejects_reordered_dto_even_when_quotes_are_identical(
    pure, reorder
):
    _, snapshot = pure
    original = sources()
    roundtrip = json.loads(
        json.dumps(
            {"sections": [list(pair) for pair in original[0].sections.items()]},
            sort_keys=True,
        )
    )
    assert list(snapshot._snapshot_content(roundtrip).items()) == list(
        original[0].sections.items()
    )
    received = copy.deepcopy(original)
    if reorder == "sources":
        received.reverse()
        # Date/title/text envelopes are identical; inference cannot recover which
        # full timestamp/ref/version the sender selected.
        assert citations.render_quotes(
            [
                (
                    next(iter(source.sections.values())),
                    source.source_at,
                    next(iter(source.sections)),
                )
                for source in original
            ],
            mode="selected",
            source_count=2,
            current_through=max(item.source_at for item in sources()),
        ) == citations.render_quotes(
            [
                (
                    next(iter(source.sections.values())),
                    source.source_at,
                    next(iter(source.sections)),
                )
                for source in received
            ],
            mode="selected",
            source_count=2,
            current_through=max(item.source_at for item in sources()),
        )
    else:
        for source in received:
            source.sections = dict(reversed(list(source.sections.items())))
    assert citations.enumeration_digest(original) != citations.enumeration_digest(
        received
    )
    with pytest.raises(citations.CitationDenied):
        resolve(payload=payload(), result=result(received))


def test_full_context_witness_preserves_unselected_source_and_jsonb_order(pure):
    canonical, _ = pure
    supplied = sources()
    third = copy.deepcopy(supplied[-1])
    third.source_ref, third.source_version = "visit_note_" + "c" * 26, 3
    supplied.append(third)
    context, body = result(supplied), payload()
    body["reviewed_excerpts"]["enumeration_sha256"] = citations.enumeration_digest(
        context.reviewed_sources
    )
    quotes = [
        (next(iter(s.sections.values())), s.source_at, next(iter(s.sections)))
        for s in context.reviewed_sources[:2]
    ]
    body["response"]["content"] = citations.render_quotes(
        quotes, mode="selected", source_count=3, current_through=context.current_through
    )
    bound = resolve(payload=body, result=context)
    roundtrip = citations.canonical_bindings(
        json.loads(json.dumps(bound, sort_keys=True))
    )
    assert [s["note_ref"] for s in roundtrip["context_witness"]["sources"]] == [
        s.source_ref for s in context.reviewed_sources
    ]
    assert len(bound["excerpts"]) == 2 and len(bound["context_witness"]["sources"]) == 3
    altered = copy.deepcopy(bound)
    altered["context_witness"]["sources"][2]["review_ref"] = "review_ref_" + "f" * 26
    assert canonical._request_digest(
        canonical._canonical_payload(body), bound
    ) != canonical._request_digest(canonical._canonical_payload(body), altered)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "unknown",
        "marker",
        "digest",
        "duplicate_source",
        "missing_source",
        "artifact",
        "review_time",
        "naive",
        "integer_included",
    ],
)
def test_closed_full_witness_rejects_missing_forged_or_ambiguous_metadata(pure, change):
    bound = resolve(payload=payload(), result=result())
    context = bound["context_witness"]
    if change == "missing":
        bound.pop("context_witness")
    elif change == "unknown":
        context["result_scalars"]["claimed_authority"] = True
    elif change == "marker":
        context["response_ref"] += "extra"
    elif change == "digest":
        context["result_scalars"]["source_version_digest"] = "sha256_" + "f" * 64
    elif change == "duplicate_source":
        context["sources"][1] = copy.deepcopy(context["sources"][0])
    elif change == "missing_source":
        context["sources"].pop()
    elif change == "artifact":
        context["artifact_ref"] = "prep_artifact_" + "a" * 26
    elif change == "review_time":
        context["sources"][0]["reviewed_at"] = "2026-10-29T21:15:00Z"
    elif change == "naive":
        context["result_scalars"]["current_through"] = "2026-10-29T21:25:00"
    else:
        context["result_scalars"]["provenance"][0]["included"] = 1
    with pytest.raises(citations.CitationDenied):
        citations.canonical_bindings(bound)


@pytest.mark.parametrize("change", ["valid", "duplicate", "marker", "pair", "unknown"])
def test_closed_history_witness_is_ordered_bounded_and_final_digest_bound(pure, change):
    bound = resolve(payload=payload(), result=result())
    cutoff = datetime(2026, 10, 30, tzinfo=UTC).isoformat()
    history: dict[str, Any] = dict(
        context_ref="history_receipt_abcdefgh",
        response_ref=f"h1|{cutoff}|context_receipt_abcdefgh",
        request_digest="a" * 64,
        cutoff=cutoff,
        dependency_ref="context_receipt_abcdefgh",
        source_version_digest="sha256_" + "d" * 64,
        members=[
            dict(
                turn_ref="turn_ref_abcdefgh",
                user_digest="b" * 64,
                final_digest="c" * 64,
            )
        ],
    )
    bound["history_witness"] = history
    if change == "duplicate":
        history["members"] *= 2
    elif change == "marker":
        history["response_ref"] += "extra"
    elif change == "pair":
        history["source_version_digest"] = None
    elif change == "unknown":
        history["members"][0]["inferred_member"] = True
    if change == "valid":
        parsed = citations.canonical_bindings(bound)
        history["cutoff"] = cutoff.replace("+00:00", "Z")
        assert parsed["history_witness"] == history
        assert len(parsed["history_witness"]["members"]) == 1
    else:
        with pytest.raises(citations.CitationDenied):
            citations.canonical_bindings(bound)
