#!/usr/bin/env python3
"""Validate and consume Cosmaneura agent handoffs without network access."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_INVALID = 4
EXIT_NOT_FOUND = 5
EXIT_AMBIGUOUS = 6

ASSESSMENT_VERSION = "cosmaneura.agent-handoff.v1"
RESOLUTION_VERSION = "cosmaneura.agent-resolution.v1"
CONTEXT_VERSION = "cosmaneura.agent-broker-context.v1"
SCHEMA_FILES = {
    ASSESSMENT_VERSION: "cosmaneura-agent-handoff-v1.schema.json",
    RESOLUTION_VERSION: "cosmaneura-agent-resolution-v1.schema.json",
    CONTEXT_VERSION: "cosmaneura-agent-broker-context-v1.schema.json",
}
SCHEMA_DIRECTORY = Path(__file__).resolve().parents[1] / "schemas"

SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MODEL_DRAFT_VERSION = "cosmaneura.agent-assessment-draft.v1"
MODEL_DRAFT_FIELDS = {
    "draft_version",
    "kind",
    "coverage",
    "decision",
    "payload",
    "actions",
    "constraints",
}
TRUSTED_FIELDS = {
    "schema_version",
    "handoff_id",
    "producer",
    "subject",
    "policy_sha",
    "generated_at",
    "input_digest",
    "artifact_digest",
    "integrity",
}
KIND_AGENTS = {
    "deployment_assessment": "deploy-navigator",
    "migration_assessment": "migration-sentinel",
    "release_assessment": "release-medic",
    "security_assessment": "security-vulnerability-sentinel",
}
RISK_TIERS = {"T0": 0, "T1": 1, "T2": 2}
MAX_EVIDENCE_FRESHNESS = timedelta(hours=2)
SCANNER_NAMES = {
    "CodeQL/SAST",
    "dependency",
    "secret",
    "IaC",
    "SBOM",
    "container",
}


class InputError(Exception):
    """The requested local input could not be read or parsed."""


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate object key {key!r}")
        value[key] = item
    return value


def _path(parent: str, child: str | int) -> str:
    if isinstance(child, int):
        return f"{parent}[{child}]"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", child):
        return f"{parent}.{child}"
    return f"{parent}[{json.dumps(child)}]"


def _json_type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return False


def _resolve_reference(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"unsupported non-local schema reference: {reference}")
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    if not isinstance(current, dict):
        raise ValueError(f"schema reference is not an object: {reference}")
    return current


def _is_date_time(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_schema(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    """Evaluate the Draft 2020-12 keywords used by the checked-in contracts."""

    reference = schema.get("$ref")
    if reference is not None:
        _validate_schema(
            instance, _resolve_reference(root, reference), root, path, errors
        )

    for subschema in schema.get("allOf", []):
        _validate_schema(instance, subschema, root, path, errors)

    conditional = schema.get("if")
    if conditional is not None:
        conditional_errors: list[str] = []
        _validate_schema(instance, conditional, root, path, conditional_errors)
        branch = schema.get("then") if not conditional_errors else schema.get("else")
        if branch is not None:
            _validate_schema(instance, branch, root, path, errors)

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = (
            [expected_type] if isinstance(expected_type, str) else expected_type
        )
        if not any(_json_type_matches(instance, value) for value in allowed_types):
            errors.append(f"{path}: expected {' or '.join(allowed_types)}")
            return

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        allowed = ", ".join(repr(value) for value in schema["enum"])
        errors.append(f"{path}: expected one of {allowed}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                errors.append(f"{_path(path, name)}: required property is missing")
        if schema.get("additionalProperties") is False:
            for name in instance.keys() - properties.keys():
                errors.append(
                    f"{_path(path, name)}: additional property is not allowed"
                )
        for name, property_schema in properties.items():
            if name in instance:
                _validate_schema(
                    instance[name], property_schema, root, _path(path, name), errors
                )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} item(s)")
        if schema.get("uniqueItems"):
            canonical = [
                json.dumps(value, sort_keys=True, separators=(",", ":"))
                for value in instance
            ]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, value in enumerate(instance):
                _validate_schema(value, item_schema, root, _path(path, index), errors)

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match required pattern")
        if schema.get("format") == "date-time" and not _is_date_time(instance):
            errors.append(f"{path}: expected an RFC 3339 date-time with timezone")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: must be at least {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: must be at most {schema['maximum']}")


@lru_cache(maxsize=3)
def _load_schema(version: str) -> dict[str, Any]:
    filename = SCHEMA_FILES[version]
    try:
        schema = json.loads(
            (SCHEMA_DIRECTORY / filename).read_text(encoding="utf-8"),
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise InputError(f"cannot load contract schema {filename}: {error}") from error
    if not isinstance(schema, dict):
        raise InputError(f"contract schema {filename} is not a JSON object")
    return schema


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _assessment_semantic_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    subject = document.get("subject")
    handoff_id = document.get("handoff_id")
    if isinstance(subject, dict) and isinstance(handoff_id, str):
        head_sha = subject.get("head_sha")
        if isinstance(head_sha, str) and head_sha not in handoff_id:
            errors.append("$.handoff_id: must contain the complete subject.head_sha")

    actions = document.get("actions")
    if isinstance(actions, list):
        identifiers = [
            action.get("id") for action in actions if isinstance(action, dict)
        ]
        if len(identifiers) != len(set(identifiers)):
            errors.append("$.actions: action IDs must be unique")
        for action_index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            evidence = action.get("evidence")
            if isinstance(evidence, list):
                for evidence_index, item in enumerate(evidence):
                    if not isinstance(item, dict):
                        continue
                    start = item.get("line_start")
                    end = item.get("line_end")
                    item_path = f"$.actions[{action_index}].evidence[{evidence_index}]"
                    if end is not None and start is None:
                        errors.append(f"{item_path}.line_end: requires line_start")
                    if isinstance(start, int) and isinstance(end, int) and end < start:
                        errors.append(
                            f"{item_path}.line_end: must not precede line_start"
                        )

    coverage = document.get("coverage")
    integrity = document.get("integrity")
    if isinstance(coverage, dict) and isinstance(integrity, dict):
        fresh_until = coverage.get("evidence_fresh_until")
        generated_at = integrity.get("generated_at")
        if (
            isinstance(fresh_until, str)
            and isinstance(generated_at, str)
            and _is_date_time(fresh_until)
            and _is_date_time(generated_at)
            and _parse_time(fresh_until) < _parse_time(generated_at)
        ):
            errors.append(
                "$.coverage.evidence_fresh_until: must not precede integrity.generated_at"
            )

        supplied_digest = integrity.get("artifact_digest")
        if isinstance(supplied_digest, str):
            expected_digest = _artifact_digest(document)
            if supplied_digest != expected_digest:
                errors.append(
                    "$.integrity.artifact_digest: does not match the canonical "
                    "assessment content"
                )

    kind = document.get("kind")
    payload = document.get("payload")
    if isinstance(subject, dict) and isinstance(payload, dict):
        if kind == "deployment_assessment":
            if payload.get("target_environment") != subject.get("environment"):
                errors.append(
                    "$.payload.target_environment: must equal subject.environment"
                )
        elif kind == "release_assessment":
            if payload.get("deployed_sha") is not None and payload.get(
                "deployed_sha"
            ) != subject.get("head_sha"):
                errors.append("$.payload.deployed_sha: must equal subject.head_sha")

    decision = document.get("decision")
    if isinstance(decision, dict) and isinstance(actions, list):
        status = decision.get("status")
        blockers = [
            action
            for action in actions
            if isinstance(action, dict) and action.get("blocking") is True
        ]
        if (
            status
            in {
                "READY",
                "HEALTHY",
                "NO_FINDINGS_OBSERVED",
                "PASS_WITH_FINDINGS",
            }
            and blockers
        ):
            errors.append(
                f"$.decision.status: {status} conflicts with blocking actions"
            )
        if status == "NO_FINDINGS_OBSERVED" and actions:
            errors.append(
                "$.decision.status: NO_FINDINGS_OBSERVED requires an empty actions array"
            )
        if status == "PASS_WITH_FINDINGS" and not actions:
            errors.append(
                "$.decision.status: PASS_WITH_FINDINGS requires at least one action"
            )
        if kind == "security_assessment":
            for index, action in enumerate(actions):
                if (
                    isinstance(action, dict)
                    and action.get("severity") in {"critical", "high"}
                    and action.get("blocking") is not True
                ):
                    errors.append(
                        f"$.actions[{index}].blocking: critical/high security actions must block"
                    )
    return errors


def _context_semantic_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    generated_at = document.get("generated_at")
    fresh_until = document.get("evidence_fresh_until")
    if (
        isinstance(generated_at, str)
        and isinstance(fresh_until, str)
        and _is_date_time(generated_at)
        and _is_date_time(fresh_until)
    ):
        freshness_window = _parse_time(fresh_until) - _parse_time(generated_at)
        if freshness_window < timedelta(0):
            errors.append(
                "$.evidence_fresh_until: must not precede trusted generated_at"
            )
        elif freshness_window > MAX_EVIDENCE_FRESHNESS:
            errors.append(
                "$.evidence_fresh_until: must be no more than 2 hours after "
                "trusted generated_at"
            )

    policy = document.get("evidence_policy")
    if not isinstance(policy, dict):
        return errors
    allowed = policy.get("allowed_action_evidence_locators")
    manifest = policy.get("evidence_manifest")
    if not isinstance(allowed, list) or not isinstance(manifest, list):
        return errors

    allowed_locators = {item for item in allowed if isinstance(item, str)}
    manifest_items = [item for item in manifest if isinstance(item, dict)]
    manifest_locators = [
        item.get("locator")
        for item in manifest_items
        if isinstance(item.get("locator"), str)
    ]
    if len(manifest_locators) != len(set(manifest_locators)):
        errors.append("$.evidence_policy.evidence_manifest: locators must be unique")
    manifest_locator_set = set(manifest_locators)
    if allowed_locators != manifest_locator_set:
        errors.append(
            "$.evidence_policy.allowed_action_evidence_locators: must exactly match "
            "the evidence_manifest locators"
        )

    manifest_by_locator = {
        item["locator"]: item
        for item in manifest_items
        if isinstance(item.get("locator"), str)
    }
    referenced: list[tuple[str, Any]] = []
    pull_request = policy.get("pull_request")
    subject = document.get("subject")
    subject_pull_request = (
        subject.get("pull_request") if isinstance(subject, dict) else None
    )
    if isinstance(pull_request, dict):
        referenced.append(
            ("$.evidence_policy.pull_request.locator", pull_request.get("locator"))
        )
        if isinstance(subject_pull_request, int):
            expected_locator = f"pull-request:{subject_pull_request}"
            if pull_request.get("locator") != expected_locator:
                errors.append(
                    "$.evidence_policy.pull_request.locator: must identify the "
                    "trusted subject pull request"
                )
        else:
            errors.append(
                "$.evidence_policy.pull_request: must be null when subject has no "
                "pull_request"
            )
    elif isinstance(subject_pull_request, int):
        errors.append(
            "$.evidence_policy.pull_request: required when subject has a pull_request"
        )
    if document.get("assessment_kind") != "release_assessment" and not isinstance(
        subject_pull_request, int
    ):
        errors.append(
            "$.subject.pull_request: required for deployment, migration, and "
            "security assessments"
        )

    checks = policy.get("checks")
    check_ids: list[int] = []
    check_locators: list[str] = []
    if isinstance(checks, list):
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                continue
            locator = check.get("locator")
            referenced.append((f"$.evidence_policy.checks[{index}].locator", locator))
            identifier = check.get("check_run_id")
            if isinstance(identifier, int) and not isinstance(identifier, bool):
                check_ids.append(identifier)
                if locator != f"check-run:{identifier}":
                    errors.append(
                        f"$.evidence_policy.checks[{index}].locator: must equal "
                        f"'check-run:{identifier}'"
                    )
            if isinstance(locator, str):
                check_locators.append(locator)
            status = check.get("status")
            conclusion = check.get("conclusion")
            if status == "completed" and conclusion is None:
                errors.append(
                    f"$.evidence_policy.checks[{index}].conclusion: completed "
                    "check requires a conclusion"
                )
            if status != "completed" and conclusion is not None:
                errors.append(
                    f"$.evidence_policy.checks[{index}].conclusion: non-completed "
                    "check must use null"
                )
        if len(check_ids) != len(set(check_ids)):
            errors.append(
                "$.evidence_policy.checks: check_run_id values must be unique"
            )
        if len(check_locators) != len(set(check_locators)):
            errors.append("$.evidence_policy.checks: locators must be unique")

    scanners = policy.get("scanner_coverage")
    scanner_names: list[str] = []
    if isinstance(scanners, list):
        for index, scanner in enumerate(scanners):
            if not isinstance(scanner, dict):
                continue
            locator = scanner.get("locator")
            referenced.append(
                (f"$.evidence_policy.scanner_coverage[{index}].locator", locator)
            )
            name = scanner.get("scanner")
            if isinstance(name, str):
                scanner_names.append(name)
            if isinstance(locator, str):
                manifest_item = manifest_by_locator.get(locator)
                status = scanner.get("status")
                expected_availability = (
                    "available" if status == "available" else "unavailable"
                )
                if (
                    isinstance(manifest_item, dict)
                    and manifest_item.get("availability") != expected_availability
                ):
                    errors.append(
                        f"$.evidence_policy.scanner_coverage[{index}].status: "
                        "conflicts with evidence_manifest availability"
                    )
        if set(scanner_names) != SCANNER_NAMES or len(scanner_names) != len(
            SCANNER_NAMES
        ):
            errors.append(
                "$.evidence_policy.scanner_coverage: must contain each required "
                "scanner exactly once"
            )

    for field in ("semantic_source", "migration_rehearsal"):
        item = policy.get(field)
        if not isinstance(item, dict):
            continue
        locator = item.get("locator")
        referenced.append((f"$.evidence_policy.{field}.locator", locator))
        manifest_item = (
            manifest_by_locator.get(locator) if isinstance(locator, str) else None
        )
        status = item.get("status")
        if field == "semantic_source":
            expected_availability = status
        else:
            expected_availability = (
                "available" if status in {"passed", "failed"} else "unavailable"
            )
        if (
            isinstance(manifest_item, dict)
            and manifest_item.get("availability") != expected_availability
        ):
            errors.append(
                f"$.evidence_policy.{field}.status: conflicts with "
                "evidence_manifest availability"
            )

    for path, locator in referenced:
        if isinstance(locator, str) and locator not in manifest_locator_set:
            errors.append(f"{path}: does not reference an evidence_manifest item")
    return errors


def _resolution_semantic_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    handoff_id = document.get("handoff_id")
    assessment = document.get("assessment")
    if isinstance(handoff_id, str) and isinstance(assessment, dict):
        head_sha = assessment.get("head_sha")
        if isinstance(head_sha, str) and head_sha not in handoff_id:
            errors.append("$.handoff_id: must contain the complete assessment.head_sha")
    resolutions = document.get("resolutions")
    if isinstance(resolutions, list):
        identifiers = [
            item.get("action_id") for item in resolutions if isinstance(item, dict)
        ]
        if len(identifiers) != len(set(identifiers)):
            errors.append("$.resolutions: action IDs must be unique")
    return errors


def _receipt_binding_errors(
    receipt: dict[str, Any], assessment: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    expected = assessment["subject"]
    actual = receipt["assessment"]
    comparisons = {
        "$.handoff_id": (receipt["handoff_id"], assessment["handoff_id"]),
        "$.assessment.repository": (
            actual["repository"],
            expected["repository"],
        ),
        "$.assessment.head_sha": (actual["head_sha"], expected["head_sha"]),
        "$.assessment.artifact_digest": (
            actual["artifact_digest"],
            assessment["integrity"]["artifact_digest"],
        ),
    }
    for path, (value, expected_value) in comparisons.items():
        if value != expected_value:
            errors.append(f"{path}: does not match the source assessment")

    expected_ids = {action["id"] for action in assessment["actions"]}
    actual_ids = {resolution["action_id"] for resolution in receipt["resolutions"]}
    for missing in sorted(expected_ids - actual_ids):
        errors.append(f"$.resolutions: missing resolution for {missing}")
    for unknown in sorted(actual_ids - expected_ids):
        errors.append(f"$.resolutions: unknown action ID {unknown}")
    return errors


def _validate_document(
    document: Any, expected_contract: str = "auto"
) -> tuple[str | None, list[str]]:
    if not isinstance(document, dict):
        return None, ["$: expected object"]
    version_field = "schema_version"
    version = document.get(version_field)
    if version is None and "context_version" in document:
        version_field = "context_version"
        version = document.get(version_field)
    if version not in SCHEMA_FILES:
        return None, [
            f"$.{version_field}: expected "
            f"{ASSESSMENT_VERSION!r}, {RESOLUTION_VERSION!r}, or {CONTEXT_VERSION!r}"
        ]
    contracts = {
        ASSESSMENT_VERSION: "assessment",
        RESOLUTION_VERSION: "resolution",
        CONTEXT_VERSION: "context",
    }
    contract = contracts[version]
    if expected_contract != "auto" and expected_contract != contract:
        return contract, [
            f"$.{version_field}: document is a {contract}, expected {expected_contract}"
        ]

    schema = _load_schema(version)
    errors: list[str] = []
    _validate_schema(document, schema, schema, "$", errors)
    if contract == "assessment":
        errors.extend(_assessment_semantic_errors(document))
    elif contract == "resolution":
        errors.extend(_resolution_semantic_errors(document))
    elif contract == "context":
        errors.extend(_context_semantic_errors(document))
    return contract, sorted(set(errors))


def _read_document(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except OSError as error:
        raise InputError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise InputError(
            f"cannot parse {path} as JSON: line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error
    except ValueError as error:
        raise InputError(f"cannot parse {path} as strict JSON: {error}") from error


def _print_validation_errors(path: Path, errors: list[str]) -> None:
    print(f"INVALID {path}", file=sys.stderr)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)


def _summary_lines(document: dict[str, Any], contract: str) -> list[str]:
    if contract == "assessment":
        actions = document["actions"]
        lines = [
            f"contract={document['schema_version']}",
            f"handoff_id={document['handoff_id']}",
            f"kind={document['kind']}",
            f"repository={document['subject']['repository']}",
            f"head_sha={document['subject']['head_sha']}",
            f"environment={document['subject']['environment']}",
            f"status={document['decision']['status']}",
            f"risk_tier={document['decision']['risk_tier']}",
            f"actions_total={len(actions)}",
            f"actions_blocking={sum(action['blocking'] for action in actions)}",
        ]
        for action in actions:
            disposition = "BLOCKING" if action["blocking"] else "ADVISORY"
            lines.append(
                f"action={action['id']}\t{action['severity']}\t{disposition}\t"
                f"{action['title']}"
            )
        return lines

    if contract == "context":
        return [
            f"contract={document['context_version']}",
            f"kind={document['assessment_kind']}",
            f"producer={document['producer']['agent']}",
            f"repository={document['subject']['repository']}",
            f"head_sha={document['subject']['head_sha']}",
            f"environment={document['subject']['environment']}",
            f"risk_tier_floor={document['risk_tier_floor']}",
            f"generated_at={document['generated_at']}",
            f"evidence_fresh_until={document['evidence_fresh_until']}",
        ]

    resolutions = document["resolutions"]
    lines = [
        f"contract={document['schema_version']}",
        f"handoff_id={document['handoff_id']}",
        f"repository={document['assessment']['repository']}",
        f"assessment_head_sha={document['assessment']['head_sha']}",
        f"new_head_sha={document['new_head_sha']}",
        f"resolutions_total={len(resolutions)}",
    ]
    for resolution in resolutions:
        lines.append(
            f"resolution={resolution['action_id']}\t{resolution['status']}\t"
            f"{resolution['explanation']}"
        )
    return lines


def _command_validate(arguments: argparse.Namespace) -> int:
    document = _read_document(arguments.path)
    contract, errors = _validate_document(document, arguments.contract)
    if not errors and arguments.assessment is not None:
        if contract != "resolution":
            errors.append("$: --assessment can only bind a resolution receipt")
        else:
            source = _read_document(arguments.assessment)
            _, source_errors = _validate_document(source, "assessment")
            if source_errors:
                errors.extend(f"source assessment {error}" for error in source_errors)
            else:
                assert isinstance(document, dict) and isinstance(source, dict)
                errors.extend(_receipt_binding_errors(document, source))
    if errors:
        _print_validation_errors(arguments.path, errors)
        return EXIT_INVALID
    assert contract is not None
    if not arguments.quiet:
        print(f"VALID {contract} {arguments.path}")
    return EXIT_OK


def _command_summarize(arguments: argparse.Namespace) -> int:
    document = _read_document(arguments.path)
    contract, errors = _validate_document(document, arguments.contract)
    if not errors and arguments.assessment is not None:
        if contract != "resolution":
            errors.append("$: --assessment can only bind a resolution receipt")
        else:
            source = _read_document(arguments.assessment)
            _, source_errors = _validate_document(source, "assessment")
            if source_errors:
                errors.extend(f"source assessment {error}" for error in source_errors)
            else:
                assert isinstance(document, dict) and isinstance(source, dict)
                errors.extend(_receipt_binding_errors(document, source))
    if errors:
        _print_validation_errors(arguments.path, errors)
        return EXIT_INVALID
    assert contract is not None and isinstance(document, dict)
    print("\n".join(_summary_lines(document, contract)))
    return EXIT_OK


def _model_draft_errors(document: Any) -> list[str]:
    if not isinstance(document, dict):
        return ["$: model draft must be an object"]
    errors: list[str] = []
    supplied_trusted = sorted(document.keys() & TRUSTED_FIELDS)
    for name in supplied_trusted:
        errors.append(f"$.{name}: model draft must not supply trusted field")
    for name in sorted(MODEL_DRAFT_FIELDS - document.keys()):
        errors.append(f"$.{name}: required model-draft property is missing")
    for name in sorted(document.keys() - MODEL_DRAFT_FIELDS - TRUSTED_FIELDS):
        errors.append(f"$.{name}: additional model-draft property is not allowed")
    if document.get("draft_version") != MODEL_DRAFT_VERSION:
        errors.append(f"$.draft_version: expected constant {MODEL_DRAFT_VERSION!r}")
    if document.get("kind") not in KIND_AGENTS:
        errors.append("$.kind: unsupported assessment kind")
    return errors


def _handoff_id(context: dict[str, Any]) -> str:
    subject = context["subject"]
    if "workflow_run_id" in subject:
        source = f"run-{subject['workflow_run_id']}"
    elif "pull_request" in subject:
        source = f"pr-{subject['pull_request']}"
    else:
        source = context["input_digest"].removeprefix("sha256:")[:16]
    return (
        f"{context['assessment_kind']}:{subject['repository']}:"
        f"{subject['head_sha']}:{source}"
    )


def _canonical_json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _artifact_digest(document: dict[str, Any]) -> str:
    preimage = copy.deepcopy(document)
    preimage["integrity"].pop("artifact_digest", None)
    return f"sha256:{hashlib.sha256(_canonical_json_bytes(preimage)).hexdigest()}"


def _assemble_assessment(
    draft: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    assessment = {
        "schema_version": ASSESSMENT_VERSION,
        "kind": context["assessment_kind"],
        "handoff_id": _handoff_id(context),
        "producer": {
            **copy.deepcopy(context["producer"]),
            "policy_sha": context["policy_sha"],
        },
        "subject": copy.deepcopy(context["subject"]),
        "coverage": copy.deepcopy(draft["coverage"]),
        "decision": copy.deepcopy(draft["decision"]),
        "payload": copy.deepcopy(draft["payload"]),
        "actions": copy.deepcopy(draft["actions"]),
        "constraints": copy.deepcopy(draft["constraints"]),
        "integrity": {
            "generated_at": context["generated_at"],
            "input_digest": context["input_digest"],
        },
    }
    # Freshness is a broker-owned authorization boundary. The model's value is
    # retained in its draft only; it can neither extend nor shorten consumption.
    assessment["coverage"]["evidence_fresh_until"] = context["evidence_fresh_until"]
    return assessment


def _normalize_model_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """Remove only the optional null placeholders required by Foundry."""

    normalized = copy.deepcopy(draft)
    kind = normalized.get("kind")
    payload = normalized.get("payload")
    if isinstance(payload, dict):
        optional_payload_field = (
            {
                "deployment_assessment": "rollback_reference",
                "release_assessment": "revision",
            }.get(kind)
            if isinstance(kind, str)
            else None
        )
        if (
            optional_payload_field is not None
            and optional_payload_field in payload
            and payload[optional_payload_field] is None
        ):
            payload.pop(optional_payload_field)

    actions = normalized.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            if (
                kind == "security_assessment"
                and "cwe" in action
                and action["cwe"] is None
            ):
                action.pop("cwe")
            evidence = action.get("evidence")
            if not isinstance(evidence, list):
                continue
            for item in evidence:
                if not isinstance(item, dict):
                    continue
                for field in ("line_start", "line_end", "digest"):
                    if field in item and item[field] is None:
                        item.pop(field)
    return normalized


def _stamp_binding_errors(draft: dict[str, Any], context: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if draft["kind"] != context["assessment_kind"]:
        errors.append("$.kind: does not match trusted context assessment_kind")
    expected_agent = KIND_AGENTS[context["assessment_kind"]]
    if context["producer"]["agent"] != expected_agent:
        errors.append(
            "$.producer.agent: does not match trusted assessment_kind "
            f"(expected {expected_agent!r})"
        )
    draft_tier = draft.get("decision", {}).get("risk_tier")
    floor = context["risk_tier_floor"]
    if draft_tier in RISK_TIERS and RISK_TIERS[draft_tier] < RISK_TIERS[floor]:
        errors.append(
            f"$.decision.risk_tier: {draft_tier} is below trusted floor {floor}"
        )

    evidence_policy = context["evidence_policy"]
    allowed_locators = set(evidence_policy["allowed_action_evidence_locators"])
    actions = draft.get("actions")
    if isinstance(actions, list):
        for action_index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            evidence_items = action.get("evidence")
            if not isinstance(evidence_items, list):
                continue
            for evidence_index, item in enumerate(evidence_items):
                if not isinstance(item, dict):
                    continue
                locator = item.get("locator")
                if isinstance(locator, str) and locator not in allowed_locators:
                    errors.append(
                        f"$.actions[{action_index}].evidence[{evidence_index}]"
                        ".locator: is not present in the trusted evidence manifest"
                    )

    payload = draft.get("payload")
    if isinstance(payload, dict):
        for field, value in payload.items():
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                if not isinstance(item, dict) or "evidence_locator" not in item:
                    continue
                locator = item.get("evidence_locator")
                if isinstance(locator, str) and locator not in allowed_locators:
                    errors.append(
                        f"$.payload.{field}[{index}].evidence_locator: is not "
                        "present in the trusted evidence manifest"
                    )
    status = draft.get("decision", {}).get("status")
    kind = context["assessment_kind"]
    if kind == "deployment_assessment" and isinstance(payload, dict):
        expected_checks = []
        for check in evidence_policy["checks"]:
            if not check["required"]:
                continue
            if check["status"] != "completed":
                normalized_status = "pending"
            elif check["conclusion"] == "success":
                normalized_status = "success"
            elif check["conclusion"] == "skipped":
                normalized_status = "skipped"
            else:
                normalized_status = "failure"
            expected_checks.append(
                {
                    "name": check["name"],
                    "status": normalized_status,
                    "evidence_locator": check["locator"],
                }
            )
        actual_checks = payload.get("required_checks")
        if isinstance(actual_checks, list):
            expected_canonical = sorted(
                _canonical_json_bytes(item) for item in expected_checks
            )
            actual_canonical = sorted(
                _canonical_json_bytes(item) for item in actual_checks
            )
            if actual_canonical != expected_canonical:
                errors.append(
                    "$.payload.required_checks: must exactly match trusted required "
                    "check statuses and locators"
                )

        if status == "READY":
            pull_request = evidence_policy["pull_request"]
            if (
                not isinstance(pull_request, dict)
                or pull_request["state"] != "open"
                or pull_request["draft"] is not False
            ):
                errors.append(
                    "$.decision.status: READY requires an open, non-draft pull request"
                )
            required_checks = [
                check for check in evidence_policy["checks"] if check["required"]
            ]
            if not required_checks:
                errors.append(
                    "$.decision.status: READY requires at least one trusted required check"
                )
            elif any(
                check["status"] != "completed" or check["conclusion"] != "success"
                for check in required_checks
            ):
                errors.append(
                    "$.decision.status: READY requires terminal success for every "
                    "trusted required check"
                )
            if any(
                item["required_for_deployment"] and item["availability"] != "available"
                for item in evidence_policy["evidence_manifest"]
            ):
                errors.append(
                    "$.decision.status: READY requires all deployment-required "
                    "evidence to be available"
                )
            if context["risk_tier_floor"] in {"T1", "T2"}:
                errors.append(
                    "$.decision.status: READY is unavailable for T1/T2 until the "
                    "broker supplies trusted feature-flag, flight-plan, and approval "
                    "evidence"
                )

    elif kind == "migration_assessment" and isinstance(payload, dict):
        rehearsal = evidence_policy["migration_rehearsal"]
        if payload.get("rehearsal_status") != rehearsal["status"]:
            errors.append(
                "$.payload.rehearsal_status: must equal trusted migration rehearsal status"
            )
        if status == "READY":
            if rehearsal["status"] != "passed":
                errors.append(
                    "$.decision.status: READY requires a trusted passed migration rehearsal"
                )
            if evidence_policy["semantic_source"]["status"] != "available":
                errors.append(
                    "$.decision.status: READY requires trusted semantic source evidence"
                )

    elif kind == "security_assessment" and isinstance(payload, dict):
        expected_scanners = [
            {
                "name": item["scanner"],
                "status": item["status"],
                "evidence_locator": item["locator"],
            }
            for item in evidence_policy["scanner_coverage"]
        ]
        actual_scanners = payload.get("scanner_inputs")
        if isinstance(actual_scanners, list):
            expected_canonical = sorted(
                _canonical_json_bytes(item) for item in expected_scanners
            )
            actual_canonical = sorted(
                _canonical_json_bytes(item) for item in actual_scanners
            )
            if actual_canonical != expected_canonical:
                errors.append(
                    "$.payload.scanner_inputs: must exactly match trusted scanner "
                    "coverage statuses and locators"
                )
        if status in {"PASS_WITH_FINDINGS", "NO_FINDINGS_OBSERVED"}:
            if evidence_policy["semantic_source"]["status"] != "available":
                errors.append(
                    f"$.decision.status: {status} requires trusted semantic source evidence"
                )
            if any(
                item["status"] != "available"
                for item in evidence_policy["scanner_coverage"]
            ):
                errors.append(
                    f"$.decision.status: {status} requires every trusted scanner "
                    "coverage status to be available"
                )

    elif kind == "release_assessment" and status == "HEALTHY":
        errors.append(
            "$.decision.status: HEALTHY is disabled until broker-owned release "
            "identity, migration, and health evidence is implemented"
        )
    return errors


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise InputError(f"output directory does not exist: {parent}")
    serialized = (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=parent, prefix=f".{path.name}.", suffix=".tmp", text=True
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = None
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise InputError(f"cannot atomically write {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _command_stamp(arguments: argparse.Namespace) -> int:
    draft = _read_document(arguments.draft)
    draft_errors = _model_draft_errors(draft)
    if draft_errors:
        _print_validation_errors(arguments.draft, draft_errors)
        return EXIT_INVALID

    context = _read_document(arguments.context)
    contract, context_errors = _validate_document(context, "context")
    if context_errors:
        _print_validation_errors(arguments.context, context_errors)
        return EXIT_INVALID
    assert contract == "context"
    assert isinstance(draft, dict) and isinstance(context, dict)

    binding_errors = _stamp_binding_errors(draft, context)
    if binding_errors:
        _print_validation_errors(arguments.context, binding_errors)
        return EXIT_INVALID

    assessment = _assemble_assessment(_normalize_model_draft(draft), context)
    assessment["integrity"]["artifact_digest"] = _artifact_digest(assessment)
    _, assessment_errors = _validate_document(assessment, "assessment")
    if assessment_errors:
        _print_validation_errors(arguments.draft, assessment_errors)
        return EXIT_INVALID

    _atomic_write_json(arguments.output, assessment)
    print(
        f"STAMPED {arguments.output} {assessment['kind']} "
        f"{assessment['subject']['repository']}@{assessment['subject']['head_sha']} "
        f"{assessment['integrity']['artifact_digest']}"
    )
    return EXIT_OK


def _looks_like_requested_assessment(
    document: Any,
    repository: str,
    sha: str,
    kind: str | None,
    handoff_id: str | None,
) -> bool:
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != ASSESSMENT_VERSION
    ):
        return False
    subject = document.get("subject")
    if not isinstance(subject, dict):
        return False
    return (
        subject.get("repository") == repository
        and subject.get("head_sha") == sha
        and (kind is None or document.get("kind") == kind)
        and (handoff_id is None or document.get("handoff_id") == handoff_id)
    )


def _command_fetch_local(arguments: argparse.Namespace) -> int:
    directory: Path = arguments.directory
    if not directory.is_dir():
        raise InputError(f"local artifact directory does not exist: {directory}")

    matches: list[tuple[Path, dict[str, Any]]] = []
    invalid_matches: list[tuple[Path, list[str]]] = []
    now = datetime.now(timezone.utc)
    for path in sorted(directory.rglob("*.json")):
        try:
            document = _read_document(path)
        except InputError:
            continue
        if not _looks_like_requested_assessment(
            document,
            arguments.repository,
            arguments.sha,
            arguments.kind,
            arguments.handoff_id,
        ):
            continue
        _, errors = _validate_document(document, "assessment")
        if not errors:
            fresh_until = _parse_time(document["coverage"]["evidence_fresh_until"])
            if fresh_until <= now:
                errors.append(
                    "$.coverage.evidence_fresh_until: assessment is expired and "
                    "cannot be consumed"
                )
        if errors:
            invalid_matches.append((path, errors))
        else:
            matches.append((path, document))

    if invalid_matches:
        for path, errors in invalid_matches:
            _print_validation_errors(path, errors)
        return EXIT_INVALID
    if not matches:
        print(
            "NOT_FOUND no valid local assessment matched "
            f"{arguments.repository}@{arguments.sha}",
            file=sys.stderr,
        )
        return EXIT_NOT_FOUND

    newest_time = max(
        _parse_time(item[1]["integrity"]["generated_at"]) for item in matches
    )
    newest = [
        item
        for item in matches
        if _parse_time(item[1]["integrity"]["generated_at"]) == newest_time
    ]
    digests = {item[1]["integrity"]["artifact_digest"] for item in newest}
    if len(digests) > 1:
        paths = ", ".join(str(item[0]) for item in newest)
        print(f"AMBIGUOUS equally recent assessments: {paths}", file=sys.stderr)
        return EXIT_AMBIGUOUS

    _, document = newest[0]
    if arguments.summary:
        print("\n".join(_summary_lines(document, "assessment")))
    else:
        print(json.dumps(document, indent=2, sort_keys=True))
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="handoffctl",
        description=(
            "Validate and consume commit-bound Cosmaneura agent handoffs. "
            "This helper performs no network or cloud operations."
        ),
        epilog=(
            "Exit codes: 0 success; 2 usage; 3 input/read error; "
            "4 invalid contract; 5 no local match; 6 ambiguous local match."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate one handoff JSON file")
    validate.add_argument("path", type=Path)
    validate.add_argument(
        "--contract",
        choices=("auto", "assessment", "resolution", "context"),
        default="auto",
    )
    validate.add_argument(
        "--assessment",
        type=Path,
        help="for a receipt, also verify exact binding and complete action coverage",
    )
    validate.add_argument("--quiet", action="store_true")
    validate.set_defaults(handler=_command_validate)

    summarize = subparsers.add_parser(
        "summarize", help="validate and print a stable worker-oriented summary"
    )
    summarize.add_argument("path", type=Path)
    summarize.add_argument(
        "--contract",
        choices=("auto", "assessment", "resolution", "context"),
        default="auto",
    )
    summarize.add_argument(
        "--assessment",
        type=Path,
        help="for a receipt, also verify exact binding and complete action coverage",
    )
    summarize.set_defaults(handler=_command_summarize)

    fetch = subparsers.add_parser(
        "fetch-local",
        help="select the newest valid local assessment for an exact repository SHA",
    )
    fetch.add_argument("directory", type=Path)
    fetch.add_argument("--repository", required=True)
    fetch.add_argument("--sha", required=True)
    fetch.add_argument(
        "--kind",
        choices=(
            "deployment_assessment",
            "migration_assessment",
            "release_assessment",
            "security_assessment",
        ),
    )
    fetch.add_argument("--handoff-id")
    fetch.add_argument("--summary", action="store_true")
    fetch.set_defaults(handler=_command_fetch_local)

    stamp = subparsers.add_parser(
        "stamp",
        help="combine a model draft with trusted broker context and write atomically",
    )
    stamp.add_argument("--draft", required=True, type=Path)
    stamp.add_argument("--context", required=True, type=Path)
    stamp.add_argument("--output", required=True, type=Path)
    stamp.set_defaults(handler=_command_stamp)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "fetch-local":
        if SHA_PATTERN.fullmatch(arguments.sha) is None:
            parser.error(
                "fetch-local --sha must be a complete lowercase 40- or 64-hex SHA"
            )
        if REPOSITORY_PATTERN.fullmatch(arguments.repository) is None:
            parser.error("fetch-local --repository must be OWNER/REPOSITORY")
    try:
        return arguments.handler(arguments)
    except InputError as error:
        print(f"INPUT_ERROR {error}", file=sys.stderr)
        return EXIT_INPUT
    except (KeyError, TypeError, ValueError) as error:
        print(f"INPUT_ERROR malformed validated document: {error}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
