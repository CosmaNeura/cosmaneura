#!/usr/bin/env python3
"""Guarded publisher and smoke-test client for Cosmaneura Foundry agents.

The implementation intentionally depends only on the Python standard library and
the authenticated Azure CLI.  It has no delete operation and its HTTP transport
only permits the Foundry calls needed to inspect agents, create immutable agent
versions, invoke them with ``store: false``, and route one exact version.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[1]
MANIFEST_DIRECTORY = HERE / "agents"
CONTRACT_DIRECTORY = HERE / "contracts"

PROJECT_ENDPOINT = "https://cosmascode.services.ai.azure.com/api/projects/proj-default"
PROJECT_NAME = "CosmasCode/proj-default"
AUTH_RESOURCE = "https://ai.azure.com"
AUTH_SCOPE = "https://ai.azure.com/.default"
API_VERSION = "v1"
MODEL_DEPLOYMENT = "gpt-5.6-sol"

APPROVED_AGENT_NAMES = frozenset(
    {
        "deploy-navigator-v1",
        "migration-sentinel-v1",
        "release-medic-v1",
        "security-vulnerability-sentinel-v1",
    }
)
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
AGENT_VERSION_PATTERN = re.compile(r"^[1-9][0-9]*$")
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


class FoundryCtlError(RuntimeError):
    """A safe, user-facing Foundry control error."""


@dataclass(frozen=True)
class AgentSpec:
    """Validated local source definition for one approved agent."""

    name: str
    policy_version: str
    description: str
    project: str
    model: str
    max_output_tokens: int
    prompt_paths: tuple[Path, ...]
    schema_path: Path
    manifest_path: Path


@dataclass(frozen=True)
class AgentBundle:
    """Rendered instructions, schema, hashes, and Foundry request payload."""

    spec: AgentSpec
    instructions: str
    instructions_sha256: str
    output_schema: dict[str, Any]
    output_schema_sha256: str
    foundry_output_schema: dict[str, Any]
    foundry_output_schema_sha256: str
    create_version_payload: dict[str, Any]


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _parse_yaml_scalar(raw: str, *, line_number: int) -> Any:
    value = raw.strip()
    if not value:
        raise FoundryCtlError(f"line {line_number}: missing scalar value")
    if value in {"[]", "{}"}:
        return json.loads(value)
    if value.startswith(('"', "'")):
        if len(value) < 2 or value[-1] != value[0]:
            raise FoundryCtlError(f"line {line_number}: unterminated quoted value")
        if value[0] == '"':
            try:
                return json.loads(value)
            except json.JSONDecodeError as error:
                raise FoundryCtlError(
                    f"line {line_number}: invalid quoted value"
                ) from error
        return value[1:-1].replace("''", "'")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        return int(value)
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]+", value):
        return float(value)
    return value


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """Load the deliberately small YAML subset used by agent manifests.

    Supported constructs are nested mappings, scalar lists, JSON-style empty
    lists/maps, quoted strings, booleans, nulls, and numbers. Unsupported YAML
    fails closed instead of being interpreted approximately.
    """

    records: list[tuple[int, int, str]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise FoundryCtlError(
                f"{path}:{line_number}: tab indentation is not supported"
            )
        content = raw_line.lstrip(" ")
        if not content or content.startswith("#"):
            continue
        indent = len(raw_line) - len(content)
        if indent % 2:
            raise FoundryCtlError(
                f"{path}:{line_number}: indentation must use two-space steps"
            )
        records.append((line_number, indent, content.rstrip()))

    if not records:
        raise FoundryCtlError(f"empty YAML document: {path}")

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if records[index][1] != indent:
            raise FoundryCtlError(f"{path}:{records[index][0]}: unexpected indentation")
        is_sequence = records[index][2].startswith("- ")
        container: Any = [] if is_sequence else {}

        while index < len(records):
            line_number, current_indent, content = records[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise FoundryCtlError(f"{path}:{line_number}: unexpected indentation")
            if content.startswith("- ") != is_sequence:
                raise FoundryCtlError(
                    f"{path}:{line_number}: cannot mix list and mapping entries"
                )

            if is_sequence:
                item = content[2:].strip()
                if not item or ":" in item:
                    raise FoundryCtlError(
                        f"{path}:{line_number}: only scalar list items are supported"
                    )
                container.append(_parse_yaml_scalar(item, line_number=line_number))
                index += 1
                continue

            key, separator, raw_value = content.partition(":")
            if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                raise FoundryCtlError(f"{path}:{line_number}: invalid mapping entry")
            if key in container:
                raise FoundryCtlError(f"{path}:{line_number}: duplicate key {key!r}")
            if raw_value.strip():
                container[key] = _parse_yaml_scalar(raw_value, line_number=line_number)
                index += 1
                continue
            index += 1
            if index >= len(records) or records[index][1] <= indent:
                raise FoundryCtlError(
                    f"{path}:{line_number}: empty mapping value for {key!r}"
                )
            child_indent = records[index][1]
            if child_indent != indent + 2:
                raise FoundryCtlError(
                    f"{path}:{records[index][0]}: nested block must indent exactly two spaces"
                )
            container[key], index = parse_block(index, child_indent)

        return container, index

    document, final_index = parse_block(0, records[0][1])
    if final_index != len(records) or not isinstance(document, dict):
        raise FoundryCtlError(f"agent manifest must be one YAML mapping: {path}")
    return document


def _required(
    mapping: Mapping[str, Any], *path: str, expected: type | tuple[type, ...]
) -> Any:
    value: Any = mapping
    traversed: list[str] = []
    for part in path:
        traversed.append(part)
        if not isinstance(value, Mapping) or part not in value:
            raise FoundryCtlError(f"manifest is missing {'.'.join(traversed)}")
        value = value[part]
    if not isinstance(value, expected) or isinstance(value, bool) and expected is int:
        type_names = (
            ", ".join(item.__name__ for item in expected)
            if isinstance(expected, tuple)
            else expected.__name__
        )
        raise FoundryCtlError(f"manifest field {'.'.join(path)} must be {type_names}")
    return value


def _resolve_manifest_path(
    manifest_path: Path, raw_path: str, *, directory: Path
) -> Path:
    path = (manifest_path.parent / raw_path).resolve()
    if not _is_within(path, directory.resolve()):
        raise FoundryCtlError(f"manifest path escapes {directory}: {raw_path}")
    if not path.is_file():
        raise FoundryCtlError(f"manifest path does not exist: {path}")
    return path


def load_agent_spec(manifest_path: Path) -> AgentSpec:
    document = _load_simple_yaml(manifest_path)
    if (
        _required(document, "schema_version", expected=str)
        != "cosmaneura.foundry-prompt-agent.v1"
    ):
        raise FoundryCtlError(f"unsupported agent manifest schema: {manifest_path}")

    name = _required(document, "agent", "id", expected=str)
    if not AGENT_NAME_PATTERN.fullmatch(name) or len(name) > 63:
        raise FoundryCtlError(f"invalid Foundry agent name: {name!r}")
    source_name = manifest_path.name.removesuffix(".agent.yaml")
    if name != f"{source_name}-v1":
        raise FoundryCtlError(f"agent id {name!r} does not match manifest filename")

    project = _required(document, "foundry", "project", expected=str)
    if project != PROJECT_NAME:
        raise FoundryCtlError(
            f"manifest targets unapproved Foundry project: {project!r}"
        )
    if (
        _required(document, "foundry", "provisioning", expected=str)
        != "existing_project_only"
    ):
        raise FoundryCtlError(
            "manifest must require existing_project_only provisioning"
        )
    if (
        _required(document, "foundry", "model", "deployment_resolution", expected=str)
        != "existing_only"
    ):
        raise FoundryCtlError("manifest must require existing_only model resolution")
    if _required(document, "foundry", "execution", "tools", expected=list):
        raise FoundryCtlError(
            "current-state prompt agents must not configure Foundry tools"
        )
    if _required(document, "output", "response_format", expected=str) != "json_schema":
        raise FoundryCtlError("agent output.response_format must be json_schema")
    if _required(document, "output", "schema_strict", expected=bool) is not True:
        raise FoundryCtlError("agent output.schema_strict must be true")

    raw_prompt_paths = _required(
        document, "instructions", "ordered_files", expected=list
    )
    if not raw_prompt_paths or not all(
        isinstance(item, str) for item in raw_prompt_paths
    ):
        raise FoundryCtlError(
            "instructions.ordered_files must be a non-empty scalar list"
        )
    prompt_paths = tuple(
        _resolve_manifest_path(manifest_path, item, directory=HERE)
        for item in raw_prompt_paths
    )
    schema_path = _resolve_manifest_path(
        manifest_path,
        _required(document, "output", "model_draft_schema_path", expected=str),
        directory=CONTRACT_DIRECTORY,
    )
    model = _required(document, "foundry", "model", "deployment_name", expected=str)
    if model != MODEL_DEPLOYMENT:
        raise FoundryCtlError(
            f"manifest targets unapproved model deployment: {model!r}"
        )

    return AgentSpec(
        name=name,
        policy_version=_required(document, "agent", "version", expected=str),
        description=_required(document, "agent", "description", expected=str),
        project=project,
        model=model,
        max_output_tokens=_required(
            document, "foundry", "model", "max_output_tokens", expected=int
        ),
        prompt_paths=prompt_paths,
        schema_path=schema_path,
        manifest_path=manifest_path.resolve(),
    )


def load_catalog(directory: Path = MANIFEST_DIRECTORY) -> dict[str, AgentSpec]:
    catalog: dict[str, AgentSpec] = {}
    for manifest_path in sorted(directory.glob("*.agent.yaml")):
        spec = load_agent_spec(manifest_path)
        if spec.name in catalog:
            raise FoundryCtlError(f"duplicate local agent id: {spec.name}")
        catalog[spec.name] = spec
    actual_names = frozenset(catalog)
    if actual_names != APPROVED_AGENT_NAMES:
        missing = sorted(APPROVED_AGENT_NAMES - actual_names)
        unexpected = sorted(actual_names - APPROVED_AGENT_NAMES)
        raise FoundryCtlError(
            f"local agent catalog differs from approved set; missing={missing}, unexpected={unexpected}"
        )
    return catalog


def _json_pointer(document: Any, fragment: str, *, reference: str) -> Any:
    if fragment in {"", "#"}:
        return document
    if not fragment.startswith("#/"):
        raise FoundryCtlError(f"unsupported JSON Schema fragment in {reference!r}")
    value = document
    for encoded_part in fragment[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise FoundryCtlError(f"JSON Schema reference does not exist: {reference}")
    return value


def bundle_schema(schema_path: Path) -> dict[str, Any]:
    """Resolve schema refs without importing the canonical final envelope.

    Draft contracts must be independently closed. References to another file are
    accepted only when that file also lives in this contracts directory. Internal
    references are inlined, then unused ``$defs`` and source-only annotations are
    removed before the schema is sent to Foundry.
    """

    document_cache: dict[Path, Any] = {}

    def load(path: Path) -> Any:
        resolved = path.resolve()
        if not _is_within(resolved, CONTRACT_DIRECTORY.resolve()):
            raise FoundryCtlError(
                f"draft schema may not import the canonical/final schema: {resolved}"
            )
        if resolved not in document_cache:
            try:
                document_cache[resolved] = json.loads(
                    resolved.read_text(encoding="utf-8"),
                    parse_constant=_reject_non_json_constant,
                    object_pairs_hook=_reject_duplicate_json_keys,
                )
            except (OSError, json.JSONDecodeError, ValueError) as error:
                raise FoundryCtlError(
                    f"cannot load JSON Schema {resolved}: {error}"
                ) from error
        return document_cache[resolved]

    def resolve(
        value: Any, source_path: Path, stack: tuple[tuple[Path, str], ...]
    ) -> Any:
        if isinstance(value, list):
            return [resolve(item, source_path, stack) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            if set(value) != {"$ref"}:
                raise FoundryCtlError(
                    "$ref siblings are not allowed in strict draft schemas"
                )
            reference = value["$ref"]
            if not isinstance(reference, str) or reference.startswith(
                ("http://", "https://")
            ):
                raise FoundryCtlError(
                    f"unsupported JSON Schema reference: {reference!r}"
                )
            file_part, separator, raw_fragment = reference.partition("#")
            target_path = (
                (source_path.parent / file_part).resolve() if file_part else source_path
            )
            target_document = load(target_path)
            fragment = f"#{raw_fragment}" if separator else ""
            key = (target_path, fragment)
            if key in stack:
                raise FoundryCtlError(
                    f"recursive JSON Schema reference is not supported: {reference}"
                )
            target = copy.deepcopy(
                _json_pointer(target_document, fragment, reference=reference)
            )
            return resolve(target, target_path, (*stack, key))

        resolved: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"$schema", "$id", "$comment", "$defs"} or key.startswith("x-"):
                continue
            resolved[key] = resolve(item, source_path, stack)
        return resolved

    root_path = schema_path.resolve()
    bundled = resolve(copy.deepcopy(load(root_path)), root_path, ((root_path, ""),))
    if not isinstance(bundled, dict):
        raise FoundryCtlError("model draft schema root must be an object")
    validate_strict_schema(bundled)
    return bundled


def validate_strict_schema(schema: Mapping[str, Any]) -> None:
    """Enforce the strict-output invariants that are easy to regress locally."""

    if schema.get("type") != "object":
        raise FoundryCtlError("strict model draft schema root must have type=object")

    unsupported = {
        "$ref",
        "allOf",
        "if",
        "then",
        "else",
        "not",
        "unevaluatedProperties",
    }

    def walk(value: Any, location: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{location}[{index}]")
            return
        if not isinstance(value, dict):
            return
        bad = unsupported.intersection(value)
        if bad:
            raise FoundryCtlError(
                f"strict model draft schema contains unsupported {sorted(bad)} at {location}"
            )
        schema_type = value.get("type")
        includes_object = schema_type == "object" or (
            isinstance(schema_type, list) and "object" in schema_type
        )
        if includes_object or "properties" in value:
            properties = value.get("properties", {})
            if not isinstance(properties, dict):
                raise FoundryCtlError(
                    f"schema properties must be an object at {location}"
                )
            if value.get("additionalProperties") is not False:
                raise FoundryCtlError(
                    f"strict object schema must set additionalProperties=false at {location}"
                )
            required = value.get("required")
            if not isinstance(required, list) or set(required) != set(properties):
                missing = sorted(set(properties) - set(required or []))
                extra = sorted(set(required or []) - set(properties))
                raise FoundryCtlError(
                    f"strict object schema must require every property at {location}; "
                    f"missing={missing}, extra={extra}"
                )
        for key, item in value.items():
            walk(item, f"{location}.{key}")

    walk(schema, "$")


FOUNDRY_UNSUPPORTED_SCHEMA_CONSTRAINTS = frozenset(
    {
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "uniqueItems",
    }
)


def project_schema_for_foundry(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Remove constraints rejected by Foundry while preserving output shape.

    The returned projection is used only for model generation. The complete
    bundled schema remains authoritative and validates every parsed response.
    Property names are never interpreted as schema keywords.
    """

    def project(rule: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rule.items():
            if key in FOUNDRY_UNSUPPORTED_SCHEMA_CONSTRAINTS:
                continue
            if key == "const":
                if "enum" in rule:
                    raise FoundryCtlError("schema may not combine const and enum")
                result["enum"] = [copy.deepcopy(value)]
                continue
            if key == "properties":
                if not isinstance(value, Mapping):
                    raise FoundryCtlError("schema properties must be an object")
                result[key] = {
                    property_name: project(property_schema)
                    for property_name, property_schema in value.items()
                    if isinstance(property_name, str)
                    and isinstance(property_schema, Mapping)
                }
                if len(result[key]) != len(value):
                    raise FoundryCtlError(
                        "schema properties contain an invalid definition"
                    )
                continue
            if key in {"items", "additionalProperties"} and isinstance(value, Mapping):
                result[key] = project(value)
                continue
            if key in {"anyOf", "oneOf"}:
                if not isinstance(value, list) or not all(
                    isinstance(item, Mapping) for item in value
                ):
                    raise FoundryCtlError(f"schema {key} must contain schema objects")
                result[key] = [project(item) for item in value]
                continue
            result[key] = copy.deepcopy(value)
        return result

    projected = project(schema)
    validate_strict_schema(projected)
    return projected


def render_bundle(spec: AgentSpec) -> AgentBundle:
    prompt_parts = []
    for path in spec.prompt_paths:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        if not text:
            raise FoundryCtlError(f"instruction file is empty: {path}")
        prompt_parts.append(text)
    instructions = "\n\n".join(prompt_parts) + "\n"
    instructions_sha256 = _sha256_text(instructions)
    output_schema = bundle_schema(spec.schema_path)
    output_schema_sha256 = _sha256_text(_canonical_json(output_schema))
    foundry_output_schema = project_schema_for_foundry(output_schema)
    foundry_output_schema_sha256 = _sha256_text(_canonical_json(foundry_output_schema))

    format_name = spec.name.replace("-", "_") + "_draft"
    payload: dict[str, Any] = {
        "description": spec.description,
        "metadata": {
            "managed_by": "foundryctl",
            "manifest_schema": "cosmaneura.foundry-prompt-agent.v1",
            "policy_version": spec.policy_version,
            "instructions_sha256": instructions_sha256,
            "output_schema_sha256": output_schema_sha256,
            "foundry_schema_sha256": foundry_output_schema_sha256,
        },
        "definition": {
            "kind": "prompt",
            "model": spec.model,
            "instructions": instructions,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": format_name,
                    "description": "Cosmaneura validated advisory assessment draft.",
                    "schema": foundry_output_schema,
                    "strict": True,
                }
            },
        },
    }
    return AgentBundle(
        spec=spec,
        instructions=instructions,
        instructions_sha256=instructions_sha256,
        output_schema=output_schema,
        output_schema_sha256=output_schema_sha256,
        foundry_output_schema=foundry_output_schema,
        foundry_output_schema_sha256=foundry_output_schema_sha256,
        create_version_payload=payload,
    )


def _validate_invoke_body(body: Mapping[str, Any]) -> None:
    expected_keys = {"agent_reference", "input", "max_output_tokens", "store"}
    if set(body) != expected_keys:
        raise FoundryCtlError(
            "agent invocation body does not have the exact permitted fields"
        )
    if body.get("store") is not False:
        raise FoundryCtlError("agent invocation must explicitly use store=false")

    reference = body.get("agent_reference")
    if not isinstance(reference, Mapping):
        raise FoundryCtlError("agent invocation omitted its agent_reference")
    allowed_reference_keys = {"type", "name"}
    if "version" in reference:
        allowed_reference_keys.add("version")
    if set(reference) != allowed_reference_keys:
        raise FoundryCtlError(
            "agent_reference does not have the exact permitted fields"
        )
    if reference.get("type") != "agent_reference":
        raise FoundryCtlError("agent_reference type must be agent_reference")
    name = reference.get("name")
    if not isinstance(name, str):
        raise FoundryCtlError("agent_reference name must be a string")
    _validate_agent_name(name)
    version = reference.get("version")
    if version is not None:
        if not isinstance(version, str):
            raise FoundryCtlError("agent_reference version must be a string")
        _validate_agent_version(version)

    input_text = body.get("input")
    if not isinstance(input_text, str):
        raise FoundryCtlError("agent invocation input must be canonical JSON text")
    if len(input_text.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise FoundryCtlError("agent invocation input exceeds the safety limit")
    try:
        evidence = json.loads(
            input_text,
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise FoundryCtlError("agent invocation input must be valid JSON") from error
    if not isinstance(evidence, dict) or _canonical_json(evidence) != input_text:
        raise FoundryCtlError(
            "agent invocation input must be one canonical JSON object"
        )

    spec = load_catalog()[name]
    if body.get("max_output_tokens") != spec.max_output_tokens:
        raise FoundryCtlError(
            "agent invocation max_output_tokens does not match the approved manifest"
        )


def _route_patch_body(version: str) -> dict[str, Any]:
    _validate_agent_version(version)
    return {
        "agent_endpoint": {
            "version_selector": {
                "version_selection_rules": [
                    {
                        "type": "FixedRatio",
                        "agent_version": version,
                        "traffic_percentage": 100,
                    }
                ]
            },
            "protocol_configuration": {"responses": {}},
            "authorization_schemes": [{"type": "Entra"}],
        }
    }


def _validate_route_body(body: Mapping[str, Any]) -> None:
    try:
        rules = body["agent_endpoint"]["version_selector"]["version_selection_rules"]
    except (KeyError, TypeError) as error:
        raise FoundryCtlError("agent routing body omitted its version rule") from error
    if (
        not isinstance(rules, list)
        or len(rules) != 1
        or not isinstance(rules[0], Mapping)
        or not isinstance(rules[0].get("agent_version"), str)
    ):
        raise FoundryCtlError("agent routing body must select one exact version")
    expected = _route_patch_body(rules[0]["agent_version"])
    if body != expected:
        raise FoundryCtlError(
            "agent routing body does not exactly match the permitted Entra route"
        )


class AzureCliRest:
    """Small Azure CLI transport with no arbitrary-URL escape hatch."""

    def __init__(self, az_binary: str = "az", runner: Runner = subprocess.run):
        self.az_binary = az_binary
        self.runner = runner

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        content_type: str = "application/json",
        allow_not_found: bool = False,
    ) -> Any:
        method = method.lower()
        self._assert_allowed_request(method, url, body, content_type)
        command = [
            self.az_binary,
            "rest",
            "--method",
            method,
            "--resource",
            AUTH_RESOURCE,
            "--url",
            url,
            "--output",
            "json",
            "--only-show-errors",
        ]
        if body is not None:
            command.extend(["--headers", f"Content-Type={content_type}"])
            command.extend(["--body", _canonical_json(body)])
        result = self.runner(command, text=True, capture_output=True, check=False)
        if result.returncode:
            combined = "\n".join(
                part for part in (result.stdout, result.stderr) if part
            ).strip()
            if allow_not_found and re.search(
                r"(?:\b404\b|ResourceNotFound|Resource Not Found|ERROR:\s*Not Found\s*\(|"
                r'"code"\s*:\s*"not_found")',
                combined,
                re.IGNORECASE,
            ):
                return None
            raise FoundryCtlError(
                f"Azure CLI request failed: {combined or 'no error text'}"
            )
        if not result.stdout.strip():
            return None
        try:
            return json.loads(
                result.stdout,
                parse_constant=_reject_non_json_constant,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise FoundryCtlError("Azure CLI returned non-JSON output") from error

    @staticmethod
    def _assert_allowed_request(
        method: str,
        url: str,
        body: Mapping[str, Any] | None,
        content_type: str,
    ) -> None:
        project_url = urlsplit(PROJECT_ENDPOINT)
        requested_url = urlsplit(url)
        if (
            requested_url.scheme != project_url.scheme
            or requested_url.netloc != project_url.netloc
            or not requested_url.path.startswith(project_url.path + "/")
            or requested_url.fragment
        ):
            raise FoundryCtlError(
                "refusing request outside the approved Foundry project endpoint"
            )
        relative = requested_url.path[len(project_url.path) :]
        try:
            query_pairs = parse_qsl(
                requested_url.query, keep_blank_values=True, strict_parsing=True
            )
        except ValueError as error:
            raise FoundryCtlError(
                "Foundry request contains an invalid query"
            ) from error
        query_values = dict(query_pairs)
        if len(query_values) != len(query_pairs):
            raise FoundryCtlError("Foundry request contains duplicate query keys")

        if method == "get" and relative == "/agents":
            if body is not None:
                raise FoundryCtlError("agent GET must not contain a body")
            allowed_keys = {"api-version", "limit"}
            if "after" in query_values:
                allowed_keys.add("after")
            if (
                set(query_values) != allowed_keys
                or query_values.get("api-version") != API_VERSION
                or query_values.get("limit") != "100"
                or "after" in query_values
                and not query_values["after"]
            ):
                raise FoundryCtlError("agent list GET has an unapproved query")
            return

        match = re.fullmatch(
            r"/agents/([A-Za-z0-9-]+)(?:/versions(?:/([1-9][0-9]*))?)?",
            relative,
        )
        if match:
            agent_name, _ = match.groups()
            _validate_agent_name(agent_name)
        else:
            agent_name = None

        if method == "get" and agent_name is not None:
            if body is not None:
                raise FoundryCtlError("agent GET must not contain a body")
            if query_values != {"api-version": API_VERSION}:
                raise FoundryCtlError("agent GET must use only GA api-version=v1")
            if relative.endswith("/versions/"):
                raise FoundryCtlError("agent version GET omitted a version")
            return

        create_match = re.fullmatch(r"/agents/([A-Za-z0-9-]+)/versions", relative)
        if method == "post" and create_match:
            agent_name = create_match.group(1)
            _validate_agent_name(agent_name)
            if (
                query_values != {"api-version": API_VERSION}
                or content_type != "application/json"
                or body is None
            ):
                raise FoundryCtlError(
                    "agent version creation must use GA api-version=v1 and a body"
                )
            expected_payload = render_bundle(
                load_catalog()[agent_name]
            ).create_version_payload
            if body != expected_payload:
                raise FoundryCtlError(
                    "agent version body does not exactly match its rendered local bundle"
                )
            return
        if method == "post" and relative == "/openai/v1/responses":
            if requested_url.query:
                raise FoundryCtlError(
                    "GA Responses endpoint must not use an api-version query"
                )
            if body is None or body.get("store") is not False:
                raise FoundryCtlError(
                    "agent invocation must explicitly use store=false"
                )
            if content_type != "application/json":
                raise FoundryCtlError("agent invocation must use application/json")
            _validate_invoke_body(body)
            return
        route_match = re.fullmatch(r"/agents/([A-Za-z0-9-]+)", relative)
        if method == "patch" and route_match:
            agent_name = route_match.group(1)
            _validate_agent_name(agent_name)
            if (
                query_values != {"api-version": API_VERSION}
                or content_type != "application/merge-patch+json"
                or body is None
            ):
                raise FoundryCtlError(
                    "agent routing must use GA api-version=v1 and a body"
                )
            _validate_route_body(body)
            return
        raise FoundryCtlError(
            f"refusing unsupported Azure mutation/request: {method.upper()} {url}"
        )


class FoundryClient:
    def __init__(self, transport: AzureCliRest):
        self.transport = transport

    @staticmethod
    def _agent_path(name: str) -> str:
        _validate_agent_name(name)
        return quote(name, safe="")

    @staticmethod
    def _version_path(version: str) -> str:
        _validate_agent_version(version)
        return quote(version, safe="")

    def get_agent(
        self, name: str, *, missing_ok: bool = False
    ) -> dict[str, Any] | None:
        encoded_name = self._agent_path(name)
        value = self.transport.request(
            "get",
            f"{PROJECT_ENDPOINT}/agents/{encoded_name}?api-version={API_VERSION}",
            allow_not_found=missing_ok,
        )
        if value is None:
            return None
        if not isinstance(value, dict):
            raise FoundryCtlError("Foundry get-agent response is not an object")
        return value

    def list_agents(self) -> list[dict[str, Any]]:
        agents: list[dict[str, Any]] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(100):
            query: dict[str, str | int] = {"api-version": API_VERSION, "limit": 100}
            if after is not None:
                query["after"] = after
            value = self.transport.request(
                "get", f"{PROJECT_ENDPOINT}/agents?{urlencode(query)}"
            )
            if not isinstance(value, dict) or not isinstance(value.get("data"), list):
                raise FoundryCtlError(
                    "Foundry list-agents response has an invalid shape"
                )
            for item in value["data"]:
                if not isinstance(item, dict):
                    raise FoundryCtlError(
                        "Foundry list-agents returned a non-object entry"
                    )
                agents.append(item)
            if not value.get("has_more"):
                return agents
            after = value.get("last_id")
            if not isinstance(after, str) or not after or after in seen_cursors:
                raise FoundryCtlError(
                    "Foundry list-agents returned an invalid pagination cursor"
                )
            seen_cursors.add(after)
        raise FoundryCtlError(
            "Foundry list-agents exceeded the pagination safety limit"
        )

    def get_version(self, name: str, version: str) -> dict[str, Any]:
        encoded_name = self._agent_path(name)
        encoded_version = self._version_path(version)
        value = self.transport.request(
            "get",
            f"{PROJECT_ENDPOINT}/agents/{encoded_name}/versions/{encoded_version}"
            f"?api-version={API_VERSION}",
        )
        if not isinstance(value, dict):
            raise FoundryCtlError("Foundry get-version response is not an object")
        return value

    def list_versions(self, name: str) -> list[dict[str, Any]]:
        encoded_name = self._agent_path(name)
        value = self.transport.request(
            "get",
            f"{PROJECT_ENDPOINT}/agents/{encoded_name}/versions?api-version={API_VERSION}",
        )
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            raise FoundryCtlError("Foundry list-versions response has an invalid shape")
        return value["data"]

    def create_initial_version(self, bundle: AgentBundle) -> dict[str, Any]:
        if self.get_agent(bundle.spec.name, missing_ok=True) is not None:
            raise FoundryCtlError(
                f"name collision: Foundry agent {bundle.spec.name!r} already exists; "
                "no version was created"
            )
        return self._post_version(bundle)

    def create_next_version(
        self, bundle: AgentBundle, *, expected_latest: str
    ) -> dict[str, Any]:
        _validate_agent_version(expected_latest)
        agent = self.get_agent(bundle.spec.name)
        if agent is None:
            raise FoundryCtlError(f"Foundry agent does not exist: {bundle.spec.name}")
        latest = _latest_version(agent)
        if latest != expected_latest:
            raise FoundryCtlError(
                f"latest-version race: expected {expected_latest}, Foundry reports {latest}"
            )
        created = self._post_version(bundle)
        if created.get("version") == expected_latest:
            raise FoundryCtlError("Foundry did not create a distinct new agent version")
        return created

    def _post_version(self, bundle: AgentBundle) -> dict[str, Any]:
        encoded_name = self._agent_path(bundle.spec.name)
        value = self.transport.request(
            "post",
            f"{PROJECT_ENDPOINT}/agents/{encoded_name}/versions?api-version={API_VERSION}",
            body=bundle.create_version_payload,
        )
        if not isinstance(value, dict):
            raise FoundryCtlError("Foundry create-version response is not an object")
        version = value.get("version")
        if not isinstance(version, str):
            raise FoundryCtlError("Foundry create-version response omitted version")
        _verify_remote_version(bundle, value, expected_version=version)
        return value

    def invoke(
        self,
        bundle: AgentBundle,
        evidence: Mapping[str, Any],
        *,
        version: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reference: dict[str, str] = {
            "type": "agent_reference",
            "name": bundle.spec.name,
        }
        if version is not None:
            _validate_agent_version(version)
            reference["version"] = version
        body = {
            "agent_reference": reference,
            "input": _canonical_json(evidence),
            "max_output_tokens": bundle.spec.max_output_tokens,
            "store": False,
        }
        response = self.transport.request(
            "post", f"{PROJECT_ENDPOINT}/openai/v1/responses", body=body
        )
        if not isinstance(response, dict):
            raise FoundryCtlError("Foundry Responses result is not an object")
        _verify_response_envelope(response, name=bundle.spec.name, version=version)
        output = parse_output_text(response)
        validation_errors = validate_json_instance(output, bundle.output_schema)
        if validation_errors:
            raise FoundryCtlError(
                "agent output failed its bundled draft schema: "
                + "; ".join(validation_errors[:10])
            )
        return response, output

    def route_version(self, bundle: AgentBundle, version: str) -> dict[str, Any]:
        _validate_agent_version(version)
        remote_version = self.get_version(bundle.spec.name, version)
        _verify_remote_version(bundle, remote_version, expected_version=version)
        body = _route_patch_body(version)
        encoded_name = self._agent_path(bundle.spec.name)
        value = self.transport.request(
            "patch",
            f"{PROJECT_ENDPOINT}/agents/{encoded_name}?api-version={API_VERSION}",
            body=body,
            content_type="application/merge-patch+json",
        )
        if not isinstance(value, dict):
            raise FoundryCtlError("Foundry route-version response is not an object")
        _verify_agent_route(value, name=bundle.spec.name, version=version)
        return value


def _validate_agent_name(name: str) -> None:
    if name not in APPROVED_AGENT_NAMES or not AGENT_NAME_PATTERN.fullmatch(name):
        raise FoundryCtlError(f"agent is not in the approved catalog: {name!r}")


def _validate_agent_version(version: str) -> None:
    if not AGENT_VERSION_PATTERN.fullmatch(version):
        raise FoundryCtlError(f"invalid immutable agent version: {version!r}")


def _latest_version(agent: Mapping[str, Any]) -> str:
    try:
        version = agent["versions"]["latest"]["version"]
    except (KeyError, TypeError) as error:
        raise FoundryCtlError(
            "Foundry agent response omitted latest version"
        ) from error
    if not isinstance(version, str):
        raise FoundryCtlError("Foundry latest version is not a string")
    return version


def _verify_remote_version(
    bundle: AgentBundle, remote: Mapping[str, Any], *, expected_version: str
) -> None:
    expected_id = f"{bundle.spec.name}:{expected_version}"
    definition = remote.get("definition")
    metadata = remote.get("metadata")
    checks = {
        "object": remote.get("object") == "agent.version",
        "id": remote.get("id") == expected_id,
        "name": remote.get("name") == bundle.spec.name,
        "version": remote.get("version") == expected_version,
        "status": remote.get("status") == "active",
        "draft": remote.get("draft") is False,
        "definition": isinstance(definition, Mapping),
        "metadata": isinstance(metadata, Mapping),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise FoundryCtlError(f"remote agent version failed verification: {failures}")
    assert isinstance(definition, Mapping)
    assert isinstance(metadata, Mapping)
    remote_instructions = definition.get("instructions")
    if (
        definition.get("kind") != "prompt"
        or definition.get("model") != bundle.spec.model
    ):
        raise FoundryCtlError(
            "remote agent definition kind/model does not match local source"
        )
    if not isinstance(remote_instructions, str) or _sha256_text(
        remote_instructions
    ) != (bundle.instructions_sha256):
        raise FoundryCtlError("remote agent instructions do not match the local bundle")
    if metadata.get("instructions_sha256") != bundle.instructions_sha256:
        raise FoundryCtlError(
            "remote agent instructions_sha256 metadata does not match"
        )
    if metadata.get("output_schema_sha256") != bundle.output_schema_sha256:
        raise FoundryCtlError(
            "remote agent output_schema_sha256 metadata does not match"
        )
    if metadata.get("foundry_schema_sha256") != bundle.foundry_output_schema_sha256:
        raise FoundryCtlError(
            "remote agent foundry_schema_sha256 metadata does not match"
        )
    if metadata.get("policy_version") != bundle.spec.policy_version:
        raise FoundryCtlError("remote agent policy_version metadata does not match")
    text_options = definition.get("text")
    response_format = (
        text_options.get("format") if isinstance(text_options, Mapping) else None
    )
    expected_format = bundle.create_version_payload["definition"]["text"]["format"]
    if not isinstance(response_format, Mapping):
        raise FoundryCtlError("remote agent omitted its strict response schema")
    if (
        response_format.get("type") != "json_schema"
        or response_format.get("name") != expected_format["name"]
        or response_format.get("strict") is not True
        or _sha256_text(_canonical_json(response_format.get("schema")))
        != bundle.foundry_output_schema_sha256
    ):
        raise FoundryCtlError(
            "remote agent strict response schema does not match local source"
        )


def _verify_agent_route(agent: Mapping[str, Any], *, name: str, version: str) -> None:
    if agent.get("object") != "agent" or agent.get("name") != name:
        raise FoundryCtlError("Foundry route response identifies the wrong agent")
    if agent.get("state") != "enabled":
        raise FoundryCtlError("Foundry routed agent is not enabled")
    endpoint = agent.get("agent_endpoint")
    if not isinstance(endpoint, Mapping):
        raise FoundryCtlError("Foundry route response omitted agent_endpoint")
    rules = (endpoint.get("version_selector") or {}).get("version_selection_rules")
    expected_rules = [
        {"type": "FixedRatio", "agent_version": version, "traffic_percentage": 100}
    ]
    if rules != expected_rules:
        raise FoundryCtlError(
            "Foundry route response does not select the exact version at 100%"
        )
    protocols = endpoint.get("protocol_configuration")
    if not isinstance(protocols, Mapping) or not isinstance(
        protocols.get("responses"), Mapping
    ):
        raise FoundryCtlError(
            "Foundry route response does not enable the Responses protocol"
        )
    schemes = endpoint.get("authorization_schemes")
    if not isinstance(schemes, list) or not any(
        isinstance(item, Mapping) and item.get("type") == "Entra" for item in schemes
    ):
        raise FoundryCtlError(
            "Foundry route response does not require Entra authorization"
        )


def _verify_response_envelope(
    response: Mapping[str, Any], *, name: str, version: str | None
) -> None:
    if response.get("object") != "response" or response.get("status") != "completed":
        raise FoundryCtlError(
            f"agent response did not complete: status={response.get('status')!r}"
        )
    if (
        response.get("error") is not None
        or response.get("incomplete_details") is not None
    ):
        raise FoundryCtlError("agent response contains error or incomplete details")
    reference = response.get("agent_reference")
    if not isinstance(reference, Mapping) or reference.get("name") != name:
        raise FoundryCtlError(
            "agent response reference does not match the requested agent"
        )
    resolved_version = reference.get("version")
    if not isinstance(resolved_version, str) or not AGENT_VERSION_PATTERN.fullmatch(
        resolved_version
    ):
        raise FoundryCtlError("agent response omitted its resolved immutable version")
    if version is not None and resolved_version != version:
        raise FoundryCtlError(
            f"agent response resolved version {resolved_version}, expected {version}"
        )


def parse_output_text(response: Mapping[str, Any]) -> dict[str, Any]:
    output_text = response.get("output_text")
    if not isinstance(output_text, str):
        fragments: list[str] = []
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if (
                        isinstance(part, Mapping)
                        and part.get("type") == "output_text"
                        and isinstance(part.get("text"), str)
                    ):
                        fragments.append(part["text"])
        output_text = "".join(fragments)
    if not output_text:
        raise FoundryCtlError("agent response did not contain output_text")
    try:
        value = json.loads(
            output_text,
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise FoundryCtlError("agent output_text is not valid JSON") from error
    if not isinstance(value, dict):
        raise FoundryCtlError("agent output_text JSON must be an object")
    return value


def validate_json_instance(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    """Validate the JSON Schema subset emitted by ``bundle_schema``."""

    errors: list[str] = []

    def fail(location: str, message: str) -> None:
        if len(errors) < 100:
            errors.append(f"{location}: {message}")

    def matches_type(value: Any, expected: str) -> bool:
        return {
            "null": value is None,
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
        }.get(expected, True)

    def check(value: Any, rule: Mapping[str, Any], location: str) -> None:
        if "const" in rule and value != rule["const"]:
            fail(location, f"must equal {rule['const']!r}")
        if "enum" in rule and value not in rule["enum"]:
            fail(location, f"must be one of {rule['enum']!r}")
        if "anyOf" in rule:
            branches = rule["anyOf"]
            if not isinstance(branches, list):
                fail(location, "anyOf must be a list")
            elif not any(
                not validate_json_instance(value, branch) for branch in branches
            ):
                fail(location, "does not satisfy any allowed schema")

        expected_type = rule.get("type")
        if isinstance(expected_type, str) and not matches_type(value, expected_type):
            fail(location, f"must have type {expected_type}")
            return
        if isinstance(expected_type, list) and not any(
            isinstance(item, str) and matches_type(value, item)
            for item in expected_type
        ):
            fail(location, f"must have one of types {expected_type!r}")
            return

        if isinstance(value, dict):
            properties = rule.get("properties", {})
            required = rule.get("required", [])
            if isinstance(required, list):
                for key in required:
                    if key not in value:
                        fail(location, f"missing required property {key!r}")
            if isinstance(properties, Mapping):
                for key, item in value.items():
                    if key in properties and isinstance(properties[key], Mapping):
                        check(item, properties[key], f"{location}.{key}")
                    elif rule.get("additionalProperties") is False:
                        fail(location, f"unexpected property {key!r}")
        elif isinstance(value, list):
            if isinstance(rule.get("minItems"), int) and len(value) < rule["minItems"]:
                fail(location, f"must contain at least {rule['minItems']} items")
            if isinstance(rule.get("maxItems"), int) and len(value) > rule["maxItems"]:
                fail(location, f"must contain at most {rule['maxItems']} items")
            if rule.get("uniqueItems") is True:
                serialized = [_canonical_json(item) for item in value]
                if len(serialized) != len(set(serialized)):
                    fail(location, "must contain unique items")
            item_rule = rule.get("items")
            if isinstance(item_rule, Mapping):
                for index, item in enumerate(value):
                    check(item, item_rule, f"{location}[{index}]")
        elif isinstance(value, str):
            if (
                isinstance(rule.get("minLength"), int)
                and len(value) < rule["minLength"]
            ):
                fail(location, f"must contain at least {rule['minLength']} characters")
            if (
                isinstance(rule.get("maxLength"), int)
                and len(value) > rule["maxLength"]
            ):
                fail(location, f"must contain at most {rule['maxLength']} characters")
            pattern = rule.get("pattern")
            if isinstance(pattern, str) and re.search(pattern, value) is None:
                fail(location, f"does not match pattern {pattern!r}")
            if rule.get("format") == "date-time":
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    fail(location, "must be an ISO-8601 date-time")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if (
                isinstance(rule.get("minimum"), (int, float))
                and value < rule["minimum"]
            ):
                fail(location, f"must be >= {rule['minimum']}")
            if (
                isinstance(rule.get("maximum"), (int, float))
                and value > rule["maximum"]
            ):
                fail(location, f"must be <= {rule['maximum']}")

    check(instance, schema, "$")
    return errors


def summarize_version(version: Mapping[str, Any]) -> dict[str, Any]:
    definition = version.get("definition")
    metadata = version.get("metadata")
    return {
        "id": version.get("id"),
        "name": version.get("name"),
        "version": version.get("version"),
        "status": version.get("status"),
        "draft": version.get("draft"),
        "created_at": version.get("created_at"),
        "kind": definition.get("kind") if isinstance(definition, Mapping) else None,
        "model": definition.get("model") if isinstance(definition, Mapping) else None,
        "instructions_sha256": (
            metadata.get("instructions_sha256")
            if isinstance(metadata, Mapping)
            else None
        ),
        "output_schema_sha256": (
            metadata.get("output_schema_sha256")
            if isinstance(metadata, Mapping)
            else None
        ),
        "foundry_schema_sha256": (
            metadata.get("foundry_schema_sha256")
            if isinstance(metadata, Mapping)
            else None
        ),
    }


def summarize_agent(agent: Mapping[str, Any]) -> dict[str, Any]:
    latest = (agent.get("versions") or {}).get("latest")
    return {
        "id": agent.get("id"),
        "name": agent.get("name"),
        "state": agent.get("state"),
        "latest": summarize_version(latest) if isinstance(latest, Mapping) else None,
        "agent_endpoint": agent.get("agent_endpoint"),
    }


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _load_evidence(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise FoundryCtlError("evidence input must not be a symbolic link")
        metadata = path.stat()
    except OSError as error:
        raise FoundryCtlError(f"cannot read evidence file: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise FoundryCtlError("evidence input must be a regular file")
    if metadata.st_size > MAX_EVIDENCE_BYTES:
        raise FoundryCtlError(
            f"evidence exceeds {MAX_EVIDENCE_BYTES} byte safety limit"
        )
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise FoundryCtlError(
                f"evidence exceeds {MAX_EVIDENCE_BYTES} byte safety limit"
            )
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise FoundryCtlError(f"evidence must be strict UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise FoundryCtlError("evidence envelope root must be a JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = None
            output.write(
                json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise FoundryCtlError(f"cannot atomically write {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _agent_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("agent", choices=sorted(APPROVED_AGENT_NAMES))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render, publish, inspect, and smoke-test approved Cosmaneura Foundry agents."
    )
    parser.add_argument(
        "--az-binary", default=os.environ.get("AZURE_CLI", "az"), help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "catalog", help="List the approved local agent sources; no Azure call."
    )

    render = subparsers.add_parser(
        "render", help="Render Foundry create-version payloads locally."
    )
    render_group = render.add_mutually_exclusive_group(required=True)
    render_group.add_argument("--agent", choices=sorted(APPROVED_AGENT_NAMES))
    render_group.add_argument("--all", action="store_true")
    render.add_argument("--output-dir", type=Path)

    subparsers.add_parser("list", help="List remote Foundry agent status (GET only).")
    get = subparsers.add_parser(
        "get", help="Get remote agent/version status (GET only)."
    )
    _agent_argument(get)
    get.add_argument("--version")

    create = subparsers.add_parser(
        "create",
        help="Create the first immutable version; fails on any name collision.",
    )
    _agent_argument(create)
    create.add_argument(
        "--confirm", required=True, help="Must exactly equal the agent name."
    )

    create_all = subparsers.add_parser(
        "create-all", help="Preflight all names, then create all four initial versions."
    )
    create_all.add_argument(
        "--confirm", choices=["CREATE-4"], required=True, help="Must be CREATE-4."
    )

    version = subparsers.add_parser(
        "version",
        help="Deliberately create a later immutable version with a race guard.",
    )
    _agent_argument(version)
    version.add_argument("--expected-latest", required=True)
    version.add_argument(
        "--confirm", required=True, help="Must exactly equal AGENT:EXPECTED_LATEST."
    )

    invoke = subparsers.add_parser(
        "invoke", help="Invoke an agent using synthetic sanitized JSON and store=false."
    )
    _agent_argument(invoke)
    invoke.add_argument("--input", type=Path, required=True)
    invoke.add_argument(
        "--version", help="Pin an immutable version; omit to test routed version."
    )
    invoke.add_argument(
        "--confirm-sanitized",
        action="store_true",
        help="Confirm input contains no PHI, secrets, raw logs, or credentials.",
    )
    invoke.add_argument(
        "--draft-output",
        type=Path,
        help="Atomically write the validated model-draft JSON for the trusted broker.",
    )
    invoke.add_argument("--raw-response-out", type=Path)

    route = subparsers.add_parser(
        "route",
        help="Verify local/remote identity, then route one exact version at 100%%.",
    )
    _agent_argument(route)
    route.add_argument("--version", required=True)
    route.add_argument(
        "--confirm", required=True, help="Must exactly equal AGENT:VERSION."
    )

    return parser


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def run(
    arguments: Sequence[str] | None = None, *, runner: Runner = subprocess.run
) -> int:
    args = build_parser().parse_args(arguments)
    catalog = load_catalog()

    if args.command == "catalog":
        _print_json(
            {
                "project": PROJECT_NAME,
                "project_endpoint": PROJECT_ENDPOINT,
                "api_version": API_VERSION,
                "auth_scope": AUTH_SCOPE,
                "agents": [
                    {
                        "name": spec.name,
                        "policy_version": spec.policy_version,
                        "model": spec.model,
                        "manifest": str(
                            spec.manifest_path.relative_to(REPOSITORY_ROOT)
                        ),
                    }
                    for spec in catalog.values()
                ],
            }
        )
        return 0

    if args.command == "render":
        names = list(catalog) if args.all else [args.agent]
        if len(names) > 1 and args.output_dir is None:
            raise FoundryCtlError("render --all requires --output-dir")
        rendered: dict[str, Any] = {}
        for name in names:
            bundle = render_bundle(catalog[name])
            rendered[name] = bundle.create_version_payload
            if args.output_dir is not None:
                _write_json(
                    args.output_dir / f"{name}.create-version.json",
                    bundle.create_version_payload,
                )
        if args.output_dir is None:
            _print_json(rendered[names[0]])
        else:
            _print_json(
                {
                    "rendered": names,
                    "output_dir": str(args.output_dir.resolve()),
                }
            )
        return 0

    client = FoundryClient(AzureCliRest(args.az_binary, runner=runner))
    if args.command == "list":
        _print_json([summarize_agent(agent) for agent in client.list_agents()])
        return 0
    if args.command == "get":
        if args.version is not None:
            _validate_agent_version(args.version)
            _print_json(summarize_version(client.get_version(args.agent, args.version)))
        else:
            agent = client.get_agent(args.agent)
            assert agent is not None
            _print_json(summarize_agent(agent))
        return 0
    if args.command == "create":
        if args.confirm != args.agent:
            raise FoundryCtlError("--confirm must exactly equal the agent name")
        created = client.create_initial_version(render_bundle(catalog[args.agent]))
        _print_json(summarize_version(created))
        return 0
    if args.command == "create-all":
        bundles = [render_bundle(spec) for spec in catalog.values()]
        collisions = [
            bundle.spec.name
            for bundle in bundles
            if client.get_agent(bundle.spec.name, missing_ok=True) is not None
        ]
        if collisions:
            raise FoundryCtlError(
                f"name collision preflight failed; no agents created: {sorted(collisions)}"
            )
        created_versions = [client._post_version(bundle) for bundle in bundles]
        _print_json([summarize_version(version) for version in created_versions])
        return 0
    if args.command == "version":
        if args.confirm != f"{args.agent}:{args.expected_latest}":
            raise FoundryCtlError("--confirm must exactly equal AGENT:EXPECTED_LATEST")
        created = client.create_next_version(
            render_bundle(catalog[args.agent]), expected_latest=args.expected_latest
        )
        _print_json(summarize_version(created))
        return 0
    if args.command == "invoke":
        if not args.confirm_sanitized:
            raise FoundryCtlError(
                "--confirm-sanitized is required before any agent invocation"
            )
        evidence = _load_evidence(args.input)
        response, output = client.invoke(
            render_bundle(catalog[args.agent]), evidence, version=args.version
        )
        if args.raw_response_out is not None:
            _write_json(args.raw_response_out, response)
        if args.draft_output is not None:
            _write_json(args.draft_output, output)
        print(
            f"verified response {response.get('id')} from "
            f"{response['agent_reference']['name']}:{response['agent_reference']['version']}",
            file=sys.stderr,
        )
        _print_json(output)
        return 0
    if args.command == "route":
        if args.confirm != f"{args.agent}:{args.version}":
            raise FoundryCtlError("--confirm must exactly equal AGENT:VERSION")
        routed = client.route_version(render_bundle(catalog[args.agent]), args.version)
        _print_json(summarize_agent(routed))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def main() -> int:
    try:
        return run()
    except (FoundryCtlError, OSError) as error:
        print(f"foundryctl: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
