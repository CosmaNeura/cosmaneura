#!/usr/bin/env python3
"""Authenticate the exact post-teardown bootstrap-identity evidence offline."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import weakref
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import staging_offline_plan_runtime as offline
    from scripts import staging_sanitizer_bootstrap_teardown_evidence_grammar as grammar
except ModuleNotFoundError:  # Direct `python scripts/...` compatibility.
    import staging_offline_plan_runtime as offline
    import staging_sanitizer_bootstrap_teardown_evidence_grammar as grammar


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/"
    "restore-sanitizer-bootstrap-identity-teardown-evidence-policy.json"
)
POLICY_SHA256 = "e779112647ce1298fda1752ee0eb4f9f314c883041fc17a033c84bd11a7e21d2"
MAXIMUM_POLICY_BYTES = 128 * 1024
KIND = "sealed-authenticated-staging-sanitizer-bootstrap-identity-teardown-evidence-v1"
EVIDENCE_FILE = grammar.EVIDENCE_FILE
STATE_EVIDENCE_FILE = grammar.STATE_EVIDENCE_FILE
REVIEW_REQUEST_FILE = grammar.REVIEW_REQUEST_FILE
REVIEW_RESPONSE_FILE = grammar.REVIEW_RESPONSE_FILE
EVIDENCE_FILES = grammar.EVIDENCE_FILES
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
PROVENANCE_DOMAIN = (
    "cosmaneura:staging:sanitizer-bootstrap-identity-teardown-evidence:v1"
)
CLAIMS = {
    "retrospective_teardown_evidence_authenticated": True,
    "bootstrap_identity_absence_authenticated": True,
    "bootstrap_direct_and_inherited_rbac_absence_authenticated": True,
    "bootstrap_job_attachment_absence_authenticated": True,
    "bootstrap_database_authority_absence_authenticated": True,
    "bootstrap_state_tombstone_authenticated": True,
    "cleanup_target_met": False,
    "cleanup_target_overrun_disclosed": True,
    "current_live_state_authenticated": False,
    "bootstrap_identity_creation_authority": False,
    "bootstrap_identity_attachment_authority": False,
    "provider_mutation_authority": False,
    "database_mutation_authority": False,
    "workflow_integrated": False,
    "executable": False,
}
BASE_PUBLIC_FIELDS = (
    "kind",
    "teardown_binding_sha256",
    "policy_sha256",
    "source_commit",
    "applied_plan_sha256",
    "review_response_id",
    "rehearsal_id",
    "authenticated_at",
    "evidence_completed_at",
    "cleanup_overrun_seconds",
)
PUBLIC_FIELDS = (*BASE_PUBLIC_FIELDS, *CLAIMS)
PRIVATE_FIELDS = ("_target", "_evidence_digests", "_proof_sha256", "_seal")
_SEAL = object()


class BootstrapIdentityTeardownEvidenceFailure(RuntimeError):
    """Bounded refusal without emitting raw identity or provider evidence."""


def _fail(
    code: str,
    failure: type[RuntimeError] = BootstrapIdentityTeardownEvidenceFailure,
) -> NoReturn:
    raise failure(code) from None


def _canonical(
    value: Any,
    *,
    dumps: Callable[..., str] = json.dumps,
    errors: tuple[type[BaseException], ...] = (
        TypeError,
        ValueError,
        UnicodeEncodeError,
        RecursionError,
    ),
    fail: Callable[[str], NoReturn] = _fail,
) -> bytes:
    try:
        return dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except errors:
        fail("bootstrap_identity_teardown_evidence.json_invalid")


def _digest(value: bytes, sha256: Callable[..., Any] = hashlib.sha256) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _pretty_file_digest(value: Any) -> str:
    raw = (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _mapping(
    value: Any,
    fields: set[str],
    code: str,
    type_of: Callable[[Any], type[Any]] = type,
    dict_type: type[dict[Any, Any]] = dict,
    to_set: Callable[[Any], set[Any]] = set,
    fail: Callable[[str], NoReturn] = _fail,
) -> Mapping[str, Any]:
    if type_of(value) is not dict_type or to_set(value) != fields:
        fail(code)
    return value


def _integer(
    value: Any,
    expected: int,
    code: str,
    type_of: Callable[[Any], type[Any]] = type,
    int_type: type[int] = int,
    fail: Callable[[str], NoReturn] = _fail,
) -> None:
    if type_of(value) is not int_type or value != expected:
        fail(code)


def _same_typed(
    value: Any,
    expected: Any,
    type_of: Callable[[Any], type[Any]] = type,
    dict_type: type[dict[Any, Any]] = dict,
    list_type: type[list[Any]] = list,
    to_set: Callable[[Any], set[Any]] = set,
    pair_length: Callable[[Any], int] = len,
    pairwise: Callable[..., Any] = zip,
) -> bool:
    pending = [(value, expected)]
    while pending:
        current, wanted = pending.pop()
        if type_of(current) is not type_of(wanted):
            return False
        if type_of(wanted) is dict_type:
            if to_set(current) != to_set(wanted):
                return False
            pending.extend((current[name], item) for name, item in wanted.items())
        elif type_of(wanted) is list_type:
            if pair_length(current) != pair_length(wanted):
                return False
            pending.extend(pairwise(current, wanted, strict=True))
        elif current != wanted:
            return False
    return True


def _exact_typed(
    value: Any,
    expected: Any,
    code: str,
    same_typed: Callable[[Any, Any], bool] = _same_typed,
    fail: Callable[[str], NoReturn] = _fail,
) -> None:
    if not same_typed(value, expected):
        fail(code)


def _matches(
    pattern: re.Pattern[str],
    value: Any,
    type_of: Callable[[Any], type[Any]] = type,
    str_type: type[str] = str,
) -> bool:
    return type_of(value) is str_type and pattern.fullmatch(value) is not None


def _parse_utc(
    value: Any,
    code: str,
    type_of: Callable[[Any], type[Any]] = type,
    str_type: type[str] = str,
    utc_pattern: re.Pattern[str] = UTC_RE,
    parse: Callable[..., datetime] = datetime.strptime,
    format_datetime: Callable[..., str] = datetime.strftime,
    utc: Any = UTC,
    value_error: type[ValueError] = ValueError,
    fail: Callable[[str], NoReturn] = _fail,
) -> datetime:
    if type_of(value) is not str_type or utc_pattern.fullmatch(value) is None:
        fail(code)
    try:
        parsed = parse(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
    except value_error:
        fail(code)
    if format_datetime(parsed, "%Y-%m-%dT%H:%M:%SZ") != value:
        fail(code)
    return parsed


def _trusted_now(
    clock: Callable[[], datetime],
    type_of: Callable[[Any], type[Any]] = type,
    datetime_type: type[datetime] = datetime,
    utc: Any = UTC,
    zero_offset: timedelta = timedelta(0),
    base_error: type[Exception] = Exception,
    fail: Callable[[str], NoReturn] = _fail,
) -> datetime:
    try:
        value = clock()
    except base_error:
        fail("bootstrap_identity_teardown_evidence.clock_invalid")
    if (
        type_of(value) is not datetime_type
        or value.tzinfo is not utc
        or value.utcoffset() != zero_offset
        or value.microsecond
        or value.fold
    ):
        fail("bootstrap_identity_teardown_evidence.clock_invalid")
    return value


def _utc_now(
    now: Callable[..., datetime] = datetime.now,
    utc: Any = UTC,
) -> datetime:
    return now(utc).replace(microsecond=0)


def _validate_policy(
    value: Any,
    mapping: Callable[[Any, set[str], str], Mapping[str, Any]] = _mapping,
    integer: Callable[[Any, int, str], None] = _integer,
    exact_typed: Callable[[Any, Any, str], None] = _exact_typed,
    matches: Callable[[re.Pattern[str], Any], bool] = _matches,
    parse_utc: Callable[[Any, str], datetime] = _parse_utc,
    fail: Callable[[str], NoReturn] = _fail,
    type_of: Callable[[Any], type[Any]] = type,
    to_set: Callable[[Any], set[Any]] = set,
    to_list: Callable[[Any], list[Any]] = list,
    dict_type: type[dict[Any, Any]] = dict,
    str_type: type[str] = str,
    int_type: type[int] = int,
    any_true: Callable[[Any], bool] = any,
    duration: Callable[..., timedelta] = timedelta,
    claims: tuple[tuple[str, Any], ...] = tuple(CLAIMS.items()),
    kind: str = KIND,
    public_fields: tuple[str, ...] = PUBLIC_FIELDS,
    private_fields: tuple[str, ...] = PRIVATE_FIELDS,
    evidence_files: tuple[str, ...] = EVIDENCE_FILES,
    evidence_file: str = EVIDENCE_FILE,
    review_request_file: str = REVIEW_REQUEST_FILE,
    review_response_file: str = REVIEW_RESPONSE_FILE,
    digest_re: re.Pattern[str] = DIGEST_RE,
    sha_re: re.Pattern[str] = SHA_RE,
    uuid_re: re.Pattern[str] = UUID_RE,
    maximum_file_bytes: int = offline.MAX_FILE_BYTES,
    expected_review_binding: Callable[..., str] = grammar.expected_review_binding,
) -> dict[str, Any]:
    code = "bootstrap_identity_teardown_evidence.policy_invalid"
    document = mapping(
        value,
        {
            "schema_version",
            "policy_id",
            "closed_world",
            "source",
            "target",
            "evidence",
            "cleanup",
            "state",
            "authority",
            "independent_review",
            "claims",
            "output",
            "status",
        },
        code,
    )
    source = mapping(
        document["source"],
        {
            "repository",
            "protected_ref",
            "source_commit",
            "applied_plan_sha256",
            "decoded_plan_sha256",
            "plan_review_request_sha256",
            "plan_review_response_id",
            "plan_review_response_sha256",
            "plan_review_verdict",
        },
        code,
    )
    target = mapping(
        document["target"],
        {
            "subscription_id",
            "tenant_id",
            "resource_group",
            "identity_name",
            "identity_resource_id",
            "client_id",
            "principal_id",
            "rehearsal_id",
        },
        code,
    )
    evidence = mapping(
        document["evidence"],
        {
            "acceptance_mode",
            "caller_selects_directory_not_content",
            "regular_owner_only_files_required",
            "symlinks_or_hardlinks_permitted",
            "exact_directory_entries_required",
            "maximum_file_bytes",
            "files",
        },
        code,
    )
    cleanup = mapping(
        document["cleanup"],
        {
            "cleanup_not_after",
            "evidence_started_at",
            "evidence_completed_at",
            "started_overrun_seconds",
            "completed_overrun_seconds",
            "cleanup_target_met",
            "cleanup_target_overrun_disclosed",
            "overrun_is_not_current_authority_proof",
        },
        code,
    )
    state = mapping(
        document["state"],
        {
            "backend_key",
            "opentofu_version",
            "lineage",
            "pre_apply_serial",
            "post_apply_serial",
            "exact_resource_addresses",
            "exact_output_keys",
            "exact_passing_check_addresses",
            "teardown_evidence_sha256",
        },
        code,
    )
    authority = mapping(
        document["authority"],
        {
            "identity_count",
            "direct_or_inherited_role_assignment_count",
            "subscription_container_apps_job_count",
            "bootstrap_identity_job_attachment_count",
            "subscription_postgres_server_count",
            "entra_enabled_postgres_server_count",
            "entra_enabled_postgres_server_id",
            "bootstrap_identity_postgres_administrator_count",
        },
        code,
    )
    review = mapping(
        document["independent_review"],
        {
            "model",
            "request_file",
            "request_sha256",
            "request_input_sha256",
            "binding_id",
            "maximum_output_tokens",
            "content_filter_end_offset",
            "response_file",
            "response_id",
            "response_sha256",
            "verdict",
        },
        code,
    )
    output = mapping(
        document["output"],
        {
            "kind",
            "public_fields",
            "private_fields",
            "raw_identity_or_provider_values_public",
            "copy_pickle_json_serialization_or_mutation_permitted",
        },
        code,
    )
    expected_status = {
        "authenticator_implemented": True,
        "retrospective_teardown_authenticated": True,
        "current_live_state_authenticated": False,
        "bootstrap_identity_recreation_authorized": False,
        "bootstrap_job_attachment_implemented": False,
        "bootstrap_control_plane_adapter_implemented": False,
        "bootstrap_database_provisioner_implemented": False,
        "provider_calls_permitted": False,
        "database_calls_permitted": False,
        "workflow_integrated": False,
        "executable": False,
        "activation_ready": False,
        "public_activation_failure": (
            "activation.sanitizer_bootstrap_job_attachment_unavailable"
        ),
    }
    claim_values = mapping(document["claims"], to_set(name for name, _ in claims), code)
    status = mapping(document["status"], to_set(expected_status), code)
    files = evidence["files"]
    integer(document["schema_version"], 1, code)
    integer(state["pre_apply_serial"], 2, code)
    integer(state["post_apply_serial"], 3, code)
    integer(review["content_filter_end_offset"], 53_234, code)
    expected_authority = {
        "identity_count": 0,
        "direct_or_inherited_role_assignment_count": 0,
        "subscription_container_apps_job_count": 6,
        "bootstrap_identity_job_attachment_count": 0,
        "subscription_postgres_server_count": 5,
        "entra_enabled_postgres_server_count": 1,
        "entra_enabled_postgres_server_id": authority[
            "entra_enabled_postgres_server_id"
        ],
        "bootstrap_identity_postgres_administrator_count": 0,
    }
    for name, expected_value in expected_authority.items():
        exact_typed(authority[name], expected_value, code)
    for name, expected_value in claims:
        exact_typed(claim_values[name], expected_value, code)
    for name, expected_value in expected_status.items():
        exact_typed(status[name], expected_value, code)
    deadline = parse_utc(cleanup["cleanup_not_after"], code)
    started = parse_utc(cleanup["evidence_started_at"], code)
    completed = parse_utc(cleanup["evidence_completed_at"], code)
    if (
        document["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-teardown-evidence-v1"
        or document["closed_world"] is not True
        or source["repository"] != "CosmaNeura/cosmaneura"
        or source["protected_ref"] != "refs/heads/master"
        or not matches(sha_re, source["source_commit"])
        or any_true(
            not matches(digest_re, source[name])
            for name in (
                "applied_plan_sha256",
                "decoded_plan_sha256",
                "plan_review_request_sha256",
                "plan_review_response_sha256",
            )
        )
        or type_of(source["plan_review_response_id"]) is not str_type
        or not source["plan_review_response_id"].startswith("resp_")
        or source["plan_review_verdict"] != "APPROVED FOR FOUNDER APPLY"
        or any_true(
            not matches(uuid_re, target[name])
            for name in ("subscription_id", "tenant_id", "client_id", "principal_id")
        )
        or target["identity_name"] != "cosmaneura-staging-restore-bootstrap-job"
        or target["identity_resource_id"].casefold()
        != (
            f"/subscriptions/{target['subscription_id']}/resourceGroups/"
            f"{target['resource_group']}/providers/Microsoft.ManagedIdentity/"
            f"userAssignedIdentities/{target['identity_name']}"
        ).casefold()
        or target["rehearsal_id"] != "21506aaeec3c"
        or evidence["acceptance_mode"]
        != "exact-hash-pinned-independent-post-apply-review"
        or evidence["caller_selects_directory_not_content"] is not True
        or evidence["regular_owner_only_files_required"] is not True
        or evidence["symlinks_or_hardlinks_permitted"] is not False
        or evidence["exact_directory_entries_required"] is not True
        or type_of(evidence["maximum_file_bytes"]) is not int_type
        or not 1 <= evidence["maximum_file_bytes"] <= maximum_file_bytes
        or type_of(files) is not dict_type
        or to_set(files) != to_set(evidence_files)
        or any_true(not matches(digest_re, item) for item in files.values())
        or cleanup["cleanup_target_met"] is not False
        or cleanup["cleanup_target_overrun_disclosed"] is not True
        or cleanup["overrun_is_not_current_authority_proof"] is not True
        or started - deadline != duration(seconds=cleanup["started_overrun_seconds"])
        or completed - deadline
        != duration(seconds=cleanup["completed_overrun_seconds"])
        or not deadline < started <= completed
        or state["backend_key"] != "staging-sanitizer-bootstrap-identity.tfstate"
        or state["opentofu_version"] != "1.12.5"
        or not matches(uuid_re, state["lineage"])
        or state["exact_resource_addresses"]
        != ["managed.terraform_data.activation_guard"]
        or state["exact_output_keys"]
        != ["sanitizer_bootstrap_identity_teardown", "state_boundary"]
        or state["exact_passing_check_addresses"]
        != [
            "terraform_data.activation_guard",
            "var.sanitizer_bootstrap_rehearsal_id",
            "var.sanitizer_bootstrap_lifetime_hours",
            "var.sanitizer_bootstrap_teardown_rehearsal_id",
            "var.sanitizer_bootstrap_teardown_evidence_sha256",
        ]
        or not matches(digest_re, state["teardown_evidence_sha256"])
        or authority != expected_authority
        or not str_type(authority["entra_enabled_postgres_server_id"])
        .casefold()
        .startswith(f"/subscriptions/{target['subscription_id']}/".casefold())
        or review["model"] != "codex-auto-review"
        or review["request_file"] != review_request_file
        or review["response_file"] != review_response_file
        or review["request_sha256"] != files[review_request_file]
        or not matches(digest_re, review["request_input_sha256"])
        or not matches(digest_re, review["binding_id"])
        or type_of(review["maximum_output_tokens"]) is not int_type
        or review["maximum_output_tokens"] != 20_000
        or review["binding_id"]
        != expected_review_binding(
            evidence_sha256=files[evidence_file],
            request_input_sha256=review["request_input_sha256"],
            maximum_output_tokens=review["maximum_output_tokens"],
            model=review["model"],
        )
        or review["response_sha256"] != files[review_response_file]
        or type_of(review["response_id"]) is not str_type
        or not review["response_id"].startswith("resp_")
        or review["verdict"] != "POST-APPLY VERIFIED"
        or to_set(claim_values) != to_set(name for name, _ in claims)
        or output["kind"] != kind
        or output["public_fields"] != to_list(public_fields)
        or output["private_fields"] != to_list(private_fields)
        or output["raw_identity_or_provider_values_public"] is not False
        or output["copy_pickle_json_serialization_or_mutation_permitted"] is not False
        or to_set(status) != to_set(expected_status)
    ):
        fail(code)
    for key in (
        "started_overrun_seconds",
        "completed_overrun_seconds",
    ):
        if type_of(cleanup[key]) is not int_type or cleanup[key] <= 0:
            fail(code)
    return value


def _read_policy(
    *,
    policy_path: Path = POLICY_PATH,
    policy_sha256: str = POLICY_SHA256,
    read_checkout_file: Callable[..., bytes] = offline.read_checkout_file,
    load_json_bytes: Callable[..., Any] = offline.load_json_bytes,
    decode_json: Callable[[str], Any] = json.JSONDecoder().decode,
    validate_policy: Callable[[Any], dict[str, Any]] = _validate_policy,
    canonical: Callable[[Any], bytes] = _canonical,
    sha256: Callable[..., Any] = hashlib.sha256,
    compare_digest: Callable[[Any, Any], bool] = hmac.compare_digest,
    maximum_policy_bytes: int = MAXIMUM_POLICY_BYTES,
    failure: type[BootstrapIdentityTeardownEvidenceFailure] = (
        BootstrapIdentityTeardownEvidenceFailure
    ),
    base_error: type[Exception] = Exception,
    fail: Callable[[str], NoReturn] = _fail,
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = read_checkout_file(
            policy_path,
            label="bootstrap identity teardown evidence policy",
            maximum_bytes=maximum_policy_bytes,
        )
        if not compare_digest(sha256(raw).hexdigest(), policy_sha256):
            fail("bootstrap_identity_teardown_evidence.policy_invalid")
        parsed = load_json_bytes(
            raw, label="bootstrap identity teardown evidence policy"
        )
        independently_parsed = decode_json(raw.decode("utf-8"))
        expected_document = canonical(independently_parsed)
        if not compare_digest(canonical(parsed), expected_document):
            fail("bootstrap_identity_teardown_evidence.policy_invalid")
        if validate_policy(parsed) is not parsed:
            fail("bootstrap_identity_teardown_evidence.policy_invalid")
        if not compare_digest(canonical(parsed), expected_document):
            fail("bootstrap_identity_teardown_evidence.policy_invalid")
        return parsed, raw
    except failure:
        raise
    except base_error:
        fail("bootstrap_identity_teardown_evidence.policy_invalid")


def load_bootstrap_identity_teardown_evidence_policy() -> dict[str, Any]:
    """Load the exact checked-in retrospective teardown policy."""

    policy, _raw = _read_policy(
        policy_path=POLICY_PATH,
        policy_sha256=POLICY_SHA256,
    )
    return policy


def _binding_material(
    policy: Mapping[str, Any],
    evidence_digests: Mapping[str, str],
    *,
    policy_sha256: str = POLICY_SHA256,
    claims: tuple[tuple[str, Any], ...] = tuple(CLAIMS.items()),
    to_dict: Callable[..., dict[Any, Any]] = dict,
) -> dict[str, Any]:
    return {
        "policy_sha256": f"sha256:{policy_sha256}",
        "source_commit": policy["source"]["source_commit"],
        "applied_plan_sha256": policy["source"]["applied_plan_sha256"],
        "review_response_id": policy["independent_review"]["response_id"],
        "rehearsal_id": policy["target"]["rehearsal_id"],
        "evidence_completed_at": policy["cleanup"]["evidence_completed_at"],
        "cleanup_overrun_seconds": policy["cleanup"]["completed_overrun_seconds"],
        "target": to_dict(policy["target"]),
        "evidence_digests": to_dict(evidence_digests),
        "claims": to_dict(claims),
    }


class AuthenticatedBootstrapIdentityTeardownEvidence:
    """Immutable process-local proof of the reviewed teardown checkpoint."""

    __slots__ = (*PUBLIC_FIELDS, *PRIVATE_FIELDS, "__weakref__")

    kind: str
    teardown_binding_sha256: str
    policy_sha256: str
    source_commit: str
    applied_plan_sha256: str
    review_response_id: str
    rehearsal_id: str
    authenticated_at: datetime
    evidence_completed_at: datetime
    cleanup_overrun_seconds: int
    retrospective_teardown_evidence_authenticated: bool
    bootstrap_identity_absence_authenticated: bool
    bootstrap_direct_and_inherited_rbac_absence_authenticated: bool
    bootstrap_job_attachment_absence_authenticated: bool
    bootstrap_database_authority_absence_authenticated: bool
    bootstrap_state_tombstone_authenticated: bool
    cleanup_target_met: bool
    cleanup_target_overrun_disclosed: bool
    current_live_state_authenticated: bool
    bootstrap_identity_creation_authority: bool
    bootstrap_identity_attachment_authority: bool
    provider_mutation_authority: bool
    database_mutation_authority: bool
    workflow_integrated: bool
    executable: bool
    _target: tuple[tuple[str, str], ...]
    _evidence_digests: tuple[tuple[str, str], ...]
    _proof_sha256: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __delattr__(self, name: str) -> NoReturn:
        del name
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __repr__(self) -> str:
        return "<AuthenticatedBootstrapIdentityTeardownEvidence sealed>"

    def __copy__(self) -> NoReturn:
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("bootstrap_identity_teardown_evidence.output_invalid")

    def _is_authenticated(self) -> bool:
        return False


def _validated_candidate(
    *,
    policy: Mapping[str, Any],
    evidence_directory: Path,
    clock: Callable[[], datetime],
    policy_sha256: str = POLICY_SHA256,
    validate_policy: Callable[[Any], dict[str, Any]] = _validate_policy,
    trusted_now: Callable[[Callable[[], datetime]], datetime] = _trusted_now,
    parse_utc: Callable[[Any, str], datetime] = _parse_utc,
    validate_evidence_directory: Callable[..., Any] = (
        grammar.validate_evidence_directory
    ),
    evidence_failure: type[Exception] = grammar.EvidenceGrammarFailure,
    binding_material: Callable[..., dict[str, Any]] = _binding_material,
    canonical: Callable[[Any], bytes] = _canonical,
    decode_json: Callable[[str], Any] = json.JSONDecoder().decode,
    digest: Callable[[bytes], str] = _digest,
    compare_digest: Callable[[bytes, bytes], bool] = hmac.compare_digest,
    fail: Callable[[str], NoReturn] = _fail,
    kind: str = KIND,
    claims: tuple[tuple[str, Any], ...] = tuple(CLAIMS.items()),
    public_fields: tuple[str, ...] = PUBLIC_FIELDS,
    to_dict: Callable[..., dict[Any, Any]] = dict,
    to_tuple: Callable[..., tuple[Any, ...]] = tuple,
    to_str: Callable[[Any], str] = str,
) -> tuple[dict[str, Any], tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    expected_policy = canonical(policy)
    document = decode_json(expected_policy.decode("ascii"))
    if validate_policy(document) is not document:
        fail("bootstrap_identity_teardown_evidence.policy_invalid")
    if not compare_digest(canonical(document), expected_policy):
        fail("bootstrap_identity_teardown_evidence.policy_changed")
    target = to_tuple(document["target"].items())
    evidence_digests = to_tuple(document["evidence"]["files"].items())
    material = binding_material(
        document,
        to_dict(evidence_digests),
        policy_sha256=policy_sha256,
        claims=claims,
        to_dict=to_dict,
    )
    before = trusted_now(clock)
    completed = parse_utc(
        document["cleanup"]["evidence_completed_at"],
        "bootstrap_identity_teardown_evidence.policy_invalid",
    )
    if before < completed:
        fail("bootstrap_identity_teardown_evidence.evidence_from_future")
    try:
        validate_evidence_directory(evidence_directory, document)
    except evidence_failure as error:
        fail(to_str(error))
    after = trusted_now(clock)
    if after < before:
        fail("bootstrap_identity_teardown_evidence.clock_regressed")
    if not compare_digest(canonical(document), expected_policy):
        fail("bootstrap_identity_teardown_evidence.policy_changed")
    values: dict[str, Any] = {
        "kind": kind,
        "teardown_binding_sha256": digest(canonical(material)),
        "policy_sha256": f"sha256:{policy_sha256}",
        "source_commit": material["source_commit"],
        "applied_plan_sha256": material["applied_plan_sha256"],
        "review_response_id": material["review_response_id"],
        "rehearsal_id": material["rehearsal_id"],
        "authenticated_at": after,
        "evidence_completed_at": completed,
        "cleanup_overrun_seconds": material["cleanup_overrun_seconds"],
        **to_dict(claims),
    }
    if to_tuple(values) != public_fields:
        fail("bootstrap_identity_teardown_evidence.output_invalid")
    return (
        values,
        target,
        evidence_digests,
    )


def _build_provenance(
    *,
    read_policy: Callable[[], tuple[dict[str, Any], bytes]] = _read_policy,
    validated_candidate: Callable[..., Any] = _validated_candidate,
    clock: Callable[[], datetime] = _utc_now,
    policy_sha256: str = POLICY_SHA256,
    validate_policy: Callable[[Any], dict[str, Any]] = _validate_policy,
    trusted_now: Callable[[Callable[[], datetime]], datetime] = _trusted_now,
    parse_utc: Callable[[Any, str], datetime] = _parse_utc,
    validate_evidence_directory: Callable[..., Any] = (
        grammar.validate_evidence_directory
    ),
    evidence_failure: type[Exception] = grammar.EvidenceGrammarFailure,
    binding_material: Callable[..., dict[str, Any]] = _binding_material,
    canonical: Callable[[Any], bytes] = _canonical,
    decode_json: Callable[[str], Any] = json.JSONDecoder().decode,
    digest: Callable[[bytes], str] = _digest,
    fail: Callable[[str], NoReturn] = _fail,
    kind: str = KIND,
    claims: tuple[tuple[str, Any], ...] = tuple(CLAIMS.items()),
    public_fields: tuple[str, ...] = PUBLIC_FIELDS,
    private_fields: tuple[str, ...] = PRIVATE_FIELDS,
    provenance_domain: str = PROVENANCE_DOMAIN,
    seal: object = _SEAL,
    process_id: Callable[[], int] = os.getpid,
    register_at_fork: Callable[..., None] = os.register_at_fork,
) -> tuple[Callable[..., Any], Callable[..., bool], Callable[..., dict[str, Any]]]:
    random_bytes = os.urandom
    hmac_new = hmac.new
    compare_digest = hmac.compare_digest
    sha256 = hashlib.sha256
    datetime_type = datetime
    format_datetime = datetime.strftime
    utc = UTC
    zero_offset = timedelta(0)
    type_of, type_read_attr = type, type.__getattribute__
    identity, pair_length, any_true, enumerate_items = id, len, any, enumerate
    tuple_type, mapping_type, bytes_type = tuple, dict, bytes
    scalar_types = (str, bool, int)
    type_error, value_error, base_error = TypeError, ValueError, Exception
    new_value = object.__new__
    key = random_bytes(32)
    value_type = AuthenticatedBootstrapIdentityTeardownEvidence
    registry_type = weakref.WeakKeyDictionary
    mappingproxy_type = type_of(type_read_attr(value_type, "__dict__"))
    namespace_contains = type_read_attr(mappingproxy_type, "__contains__")
    namespace_getitem = type_read_attr(mappingproxy_type, "__getitem__")

    def capture_namespaces(target: type[Any]) -> tuple[Any, tuple[Any, ...]]:
        mro = type_read_attr(target, "__mro__")
        return mro, tuple_type(
            (owner, type_read_attr(owner, "__dict__")) for owner in mro
        )

    def raw_resolve(namespaces: tuple[Any, ...], name: str) -> tuple[Any, Any]:
        for owner, namespace in namespaces:
            if namespace_contains(namespace, name):
                return owner, namespace_getitem(namespace, name)
        raise value_error

    def captured_attributes(
        namespaces: tuple[Any, ...], names: tuple[str, ...]
    ) -> tuple[tuple[str, Any, Any], ...]:
        return tuple_type((name, *raw_resolve(namespaces, name)) for name in names)

    value_mro, value_namespaces = capture_namespaces(value_type)
    registry_mro, registry_namespaces = capture_namespaces(registry_type)
    registry_operations = captured_attributes(
        registry_namespaces, ("get", "__setitem__", "pop", "clear")
    )
    registry_get, registry_set, registry_pop, registry_clear = (
        item[2] for item in registry_operations
    )
    slot_descriptors = tuple_type(
        (name, raw_resolve(value_namespaces, name)[1])
        for name in (*public_fields, *private_fields)
    )
    member_descriptor_type = type_of(slot_descriptors[0][1])
    descriptor_get = type_read_attr(member_descriptor_type, "__get__")
    descriptor_set = type_read_attr(member_descriptor_type, "__set__")
    guarded_class_attributes = captured_attributes(
        value_namespaces,
        ("__getattribute__", "__hash__", "__eq__", *public_fields, *private_fields),
    )
    issued: weakref.WeakKeyDictionary[
        AuthenticatedBootstrapIdentityTeardownEvidence,
        tuple[int, bytes, tuple[tuple[str, Any], ...]],
    ] = registry_type()

    def class_shape_intact() -> bool:
        try:

            def unchanged(
                target: type[Any],
                expected_mro: tuple[Any, ...],
                namespaces: tuple[Any, ...],
                attributes: tuple[tuple[str, Any, Any], ...],
            ) -> bool:
                if type_read_attr(target, "__mro__") is not expected_mro:
                    return False
                for name, owner, expected in attributes:
                    current_owner, current = raw_resolve(namespaces, name)
                    if current_owner is not owner or current is not expected:
                        return False
                return True

            return unchanged(
                value_type, value_mro, value_namespaces, guarded_class_attributes
            ) and unchanged(
                registry_type, registry_mro, registry_namespaces, registry_operations
            )
        except base_error:
            return False

    def read_slot(
        value: AuthenticatedBootstrapIdentityTeardownEvidence, name: str
    ) -> Any:
        for slot_name, descriptor in slot_descriptors:
            if slot_name == name:
                return descriptor_get(descriptor, value, value_type)
        raise value_error

    def write_slot(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
        name: str,
        item: Any,
    ) -> None:
        for slot_name, descriptor in slot_descriptors:
            if slot_name == name:
                descriptor_set(descriptor, value, item)
                return
        raise value_error

    def valid_public_projection(value: object) -> bool:
        return (
            type_of(value) is tuple_type
            and pair_length(value) == pair_length(public_fields)
            and not any_true(
                type_of(pair) is not tuple_type
                or pair_length(pair) != 2
                or type_of(pair[0]) is not scalar_types[0]
                or pair[0] != public_fields[index]
                or type_of(pair[1]) not in scalar_types
                for index, pair in enumerate_items(value)
            )
        )

    def reset_after_fork() -> None:
        nonlocal key
        registry_clear(issued)
        key = random_bytes(32)

    register_at_fork(after_in_child=reset_after_fork)

    def snapshot(value: AuthenticatedBootstrapIdentityTeardownEvidence) -> bytes:
        if type_of(value) is not value_type or not class_shape_intact():
            raise type_error

        encoded_public: list[dict[str, Any]] = []
        for name in public_fields:
            item = read_slot(value, name)
            if type_of(item) is datetime_type:
                if (
                    item.tzinfo is not utc
                    or item.utcoffset() != zero_offset
                    or item.microsecond
                    or item.fold
                ):
                    raise value_error
                encoded = {
                    "type": "utc-datetime-seconds",
                    "value": item.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            elif type_of(item) in scalar_types:
                encoded = {"type": type_of(item).__name__, "value": item}
            else:
                raise type_error
            encoded_public.append({"name": name, **encoded})

        def exact_pairs(name: str, value: object) -> dict[str, Any]:
            if type_of(value) is not tuple_type:
                raise type_error
            if any_true(
                type_of(pair) is not tuple_type
                or pair_length(pair) != 2
                or type_of(pair[0]) is not scalar_types[0]
                or type_of(pair[1]) is not scalar_types[0]
                for pair in value
            ):
                raise type_error
            return {"name": name, "type": "ordered-string-pairs", "value": value}

        return canonical(
            {
                "public": encoded_public,
                "private": [
                    exact_pairs("_target", read_slot(value, "_target")),
                    exact_pairs(
                        "_evidence_digests", read_slot(value, "_evidence_digests")
                    ),
                    {
                        "name": "_seal",
                        "type": "object-identity",
                        "value": identity(read_slot(value, "_seal")),
                    },
                ],
            }
        )

    def proof(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
        expected_snapshot: bytes,
        public_projection: tuple[tuple[str, Any], ...],
    ) -> str:
        material = canonical(
            {
                "domain": provenance_domain,
                "pid": process_id(),
                "object_identity": identity(value),
                "snapshot_sha256": sha256(expected_snapshot).hexdigest(),
                "public_projection": public_projection,
                "seal_identity": identity(seal),
            }
        )
        return hmac_new(key, material, sha256).hexdigest()

    def verified_record(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
    ) -> tuple[int, bytes, tuple[tuple[str, Any], ...]] | None:
        try:
            if (
                type_of(value) is not value_type
                or not class_shape_intact()
                or read_slot(value, "_seal") is not seal
            ):
                return None
            record = registry_get(issued, value)
            if type_of(record) is not tuple_type or pair_length(record) != 3:
                return None
            issued_pid, expected_snapshot, public_projection = record
            if (
                type_of(issued_pid) is not scalar_types[2]
                or type_of(expected_snapshot) is not bytes_type
                or not valid_public_projection(public_projection)
            ):
                return None
            current_snapshot = snapshot(value)
            if (
                issued_pid == process_id()
                and compare_digest(current_snapshot, expected_snapshot)
                and type_of(read_slot(value, "_proof_sha256")) is scalar_types[0]
                and compare_digest(
                    read_slot(value, "_proof_sha256"),
                    proof(value, expected_snapshot, public_projection),
                )
            ):
                return record
        except base_error:
            pass
        return None

    def is_authenticated(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
    ) -> bool:
        return verified_record(value) is not None

    def project(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
    ) -> dict[str, Any]:
        record = verified_record(value)
        if record is None:
            fail("bootstrap_identity_teardown_evidence.output_invalid")
        return mapping_type(record[2])

    def authenticate(
        *, evidence_directory: Path
    ) -> AuthenticatedBootstrapIdentityTeardownEvidence:
        policy, _raw = read_policy()
        public, target, evidence_digests = validated_candidate(
            policy=policy,
            evidence_directory=evidence_directory,
            clock=clock,
            policy_sha256=policy_sha256,
            validate_policy=validate_policy,
            trusted_now=trusted_now,
            parse_utc=parse_utc,
            validate_evidence_directory=validate_evidence_directory,
            evidence_failure=evidence_failure,
            binding_material=binding_material,
            canonical=canonical,
            decode_json=decode_json,
            digest=digest,
            compare_digest=compare_digest,
            fail=fail,
            kind=kind,
            claims=claims,
            public_fields=public_fields,
            to_dict=mapping_type,
            to_tuple=tuple_type,
        )
        if any_true(
            type_of(mapping_type.get(public, name)) is not type_of(expected)
            or mapping_type.get(public, name) != expected
            for name, expected in claims
        ):
            fail("bootstrap_identity_teardown_evidence.output_invalid")
        if not class_shape_intact():
            fail("bootstrap_identity_teardown_evidence.output_invalid")
        value = new_value(value_type)
        values = {
            **public,
            "_target": target,
            "_evidence_digests": evidence_digests,
            "_proof_sha256": "",
            "_seal": seal,
        }
        if tuple_type(values) != (*public_fields, *private_fields):
            fail("bootstrap_identity_teardown_evidence.output_invalid")
        for name, item in values.items():
            write_slot(value, name, item)
        expected_snapshot = snapshot(value)
        public_projection = tuple_type(
            (
                name,
                format_datetime(item, "%Y-%m-%dT%H:%M:%SZ")
                if type_of(item := read_slot(value, name)) is datetime_type
                else item,
            )
            for name in public_fields
        )
        write_slot(
            value,
            "_proof_sha256",
            proof(value, expected_snapshot, public_projection),
        )
        registry_set(
            issued, value, (process_id(), expected_snapshot, public_projection)
        )
        if not is_authenticated(value):
            registry_pop(issued, value, None)
            fail("bootstrap_identity_teardown_evidence.output_invalid")
        return value

    return authenticate, is_authenticated, project


_authenticate, _bound_is_authenticated, _bound_public_projection = _build_provenance()
AuthenticatedBootstrapIdentityTeardownEvidence._is_authenticated = (  # type: ignore[method-assign]
    _bound_is_authenticated
)


def _bind_module_verifier(
    verify: Callable[[AuthenticatedBootstrapIdentityTeardownEvidence], bool],
) -> Callable[[AuthenticatedBootstrapIdentityTeardownEvidence], bool]:
    def is_authenticated_bootstrap_identity_teardown_evidence(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
    ) -> bool:
        """Verify process-local provenance without mutable class-method dispatch."""

        return verify(value)

    return is_authenticated_bootstrap_identity_teardown_evidence


is_authenticated_bootstrap_identity_teardown_evidence = _bind_module_verifier(
    _bound_is_authenticated
)


def _bind_public_projection(
    project: Callable[[AuthenticatedBootstrapIdentityTeardownEvidence], dict[str, Any]],
) -> Callable[[AuthenticatedBootstrapIdentityTeardownEvidence], dict[str, Any]]:
    def public_projection(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
    ) -> dict[str, Any]:
        return project(value)

    return public_projection


_public_projection = _bind_public_projection(_bound_public_projection)


del _bind_module_verifier
del _bind_public_projection
del _bound_is_authenticated
del _bound_public_projection


def authenticate_bootstrap_identity_teardown_evidence(
    *, evidence_directory: Path
) -> AuthenticatedBootstrapIdentityTeardownEvidence:
    """Authenticate the exact reviewed evidence without live authority."""

    try:
        return _authenticate(evidence_directory=evidence_directory)
    except BootstrapIdentityTeardownEvidenceFailure:
        raise
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.evidence_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_directory", type=Path)
    args = parser.parse_args(argv)
    try:
        result = authenticate_bootstrap_identity_teardown_evidence(
            evidence_directory=args.evidence_directory
        )
        sys.stdout.buffer.write(_canonical(_public_projection(result)) + b"\n")
    except BootstrapIdentityTeardownEvidenceFailure as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
