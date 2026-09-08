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


def _fail(code: str) -> NoReturn:
    raise BootstrapIdentityTeardownEvidenceFailure(code) from None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("bootstrap_identity_teardown_evidence.json_invalid")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _pretty_file_digest(value: Any) -> str:
    raw = (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _integer(value: Any, expected: int, code: str) -> None:
    if type(value) is not int or value != expected:
        _fail(code)


def _same_typed(value: Any, expected: Any) -> bool:
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return set(value) == set(expected) and all(
            _same_typed(value[name], item) for name, item in expected.items()
        )
    if type(expected) is list:
        return len(value) == len(expected) and all(
            _same_typed(item, expected[index]) for index, item in enumerate(value)
        )
    return value == expected


def _exact_typed(value: Any, expected: Any, code: str) -> None:
    if not _same_typed(value, expected):
        _fail(code)


def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _parse_utc(value: Any, code: str) -> datetime:
    if type(value) is not str or UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail(code)
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return parsed


def _trusted_now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is not UTC
        or value.utcoffset() != timedelta(0)
        or value.microsecond
        or value.fold
    ):
        _fail("bootstrap_identity_teardown_evidence.clock_invalid")
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _validate_policy(value: Any) -> dict[str, Any]:
    code = "bootstrap_identity_teardown_evidence.policy_invalid"
    document = _mapping(
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
    source = _mapping(
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
    target = _mapping(
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
    evidence = _mapping(
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
    cleanup = _mapping(
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
    state = _mapping(
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
    authority = _mapping(
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
    review = _mapping(
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
    output = _mapping(
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
    claims = _mapping(document["claims"], set(CLAIMS), code)
    status = _mapping(document["status"], set(expected_status), code)
    files = evidence["files"]
    _integer(document["schema_version"], 1, code)
    _integer(state["pre_apply_serial"], 2, code)
    _integer(state["post_apply_serial"], 3, code)
    _integer(review["content_filter_end_offset"], 53_234, code)
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
        _exact_typed(authority[name], expected_value, code)
    for name, expected_value in CLAIMS.items():
        _exact_typed(claims[name], expected_value, code)
    for name, expected_value in expected_status.items():
        _exact_typed(status[name], expected_value, code)
    deadline = _parse_utc(cleanup["cleanup_not_after"], code)
    started = _parse_utc(cleanup["evidence_started_at"], code)
    completed = _parse_utc(cleanup["evidence_completed_at"], code)
    if (
        document["policy_id"]
        != "cosmaneura-staging-sanitizer-bootstrap-identity-teardown-evidence-v1"
        or document["closed_world"] is not True
        or source["repository"] != "CosmaNeura/cosmaneura"
        or source["protected_ref"] != "refs/heads/master"
        or not _matches(SHA_RE, source["source_commit"])
        or any(
            not _matches(DIGEST_RE, source[name])
            for name in (
                "applied_plan_sha256",
                "decoded_plan_sha256",
                "plan_review_request_sha256",
                "plan_review_response_sha256",
            )
        )
        or type(source["plan_review_response_id"]) is not str
        or not source["plan_review_response_id"].startswith("resp_")
        or source["plan_review_verdict"] != "APPROVED FOR FOUNDER APPLY"
        or any(
            not _matches(UUID_RE, target[name])
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
        or type(evidence["maximum_file_bytes"]) is not int
        or not 1 <= evidence["maximum_file_bytes"] <= offline.MAX_FILE_BYTES
        or type(files) is not dict
        or set(files) != set(EVIDENCE_FILES)
        or any(not _matches(DIGEST_RE, item) for item in files.values())
        or cleanup["cleanup_target_met"] is not False
        or cleanup["cleanup_target_overrun_disclosed"] is not True
        or cleanup["overrun_is_not_current_authority_proof"] is not True
        or started - deadline != timedelta(seconds=cleanup["started_overrun_seconds"])
        or completed - deadline
        != timedelta(seconds=cleanup["completed_overrun_seconds"])
        or not deadline < started <= completed
        or state["backend_key"] != "staging-sanitizer-bootstrap-identity.tfstate"
        or state["opentofu_version"] != "1.12.5"
        or not _matches(UUID_RE, state["lineage"])
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
        or not _matches(DIGEST_RE, state["teardown_evidence_sha256"])
        or authority != expected_authority
        or not str(authority["entra_enabled_postgres_server_id"])
        .casefold()
        .startswith(f"/subscriptions/{target['subscription_id']}/".casefold())
        or review["model"] != "codex-auto-review"
        or review["request_file"] != REVIEW_REQUEST_FILE
        or review["response_file"] != REVIEW_RESPONSE_FILE
        or review["request_sha256"] != files[REVIEW_REQUEST_FILE]
        or not _matches(DIGEST_RE, review["request_input_sha256"])
        or not _matches(DIGEST_RE, review["binding_id"])
        or type(review["maximum_output_tokens"]) is not int
        or review["maximum_output_tokens"] != 20_000
        or review["binding_id"]
        != grammar.expected_review_binding(
            evidence_sha256=files[EVIDENCE_FILE],
            request_input_sha256=review["request_input_sha256"],
            maximum_output_tokens=review["maximum_output_tokens"],
            model=review["model"],
        )
        or review["response_sha256"] != files[REVIEW_RESPONSE_FILE]
        or type(review["response_id"]) is not str
        or not review["response_id"].startswith("resp_")
        or review["verdict"] != "POST-APPLY VERIFIED"
        or set(claims) != set(CLAIMS)
        or output["kind"] != KIND
        or output["public_fields"] != list(PUBLIC_FIELDS)
        or output["private_fields"] != list(PRIVATE_FIELDS)
        or output["raw_identity_or_provider_values_public"] is not False
        or output["copy_pickle_json_serialization_or_mutation_permitted"] is not False
        or set(status) != set(expected_status)
    ):
        _fail(code)
    for key in (
        "started_overrun_seconds",
        "completed_overrun_seconds",
    ):
        if type(cleanup[key]) is not int or cleanup[key] <= 0:
            _fail(code)
    return dict(document)


def _read_policy() -> tuple[dict[str, Any], bytes]:
    try:
        raw = offline.read_checkout_file(
            POLICY_PATH,
            label="bootstrap identity teardown evidence policy",
            maximum_bytes=MAXIMUM_POLICY_BYTES,
        )
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), POLICY_SHA256):
            _fail("bootstrap_identity_teardown_evidence.policy_invalid")
        parsed = offline.load_json_bytes(
            raw, label="bootstrap identity teardown evidence policy"
        )
        return _validate_policy(parsed), raw
    except BootstrapIdentityTeardownEvidenceFailure:
        raise
    except Exception:
        _fail("bootstrap_identity_teardown_evidence.policy_invalid")


def load_bootstrap_identity_teardown_evidence_policy() -> dict[str, Any]:
    """Load the exact checked-in retrospective teardown policy."""

    policy, _raw = _read_policy()
    return policy


def _binding_material(
    policy: Mapping[str, Any], evidence_digests: Mapping[str, str]
) -> dict[str, Any]:
    return {
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "source_commit": policy["source"]["source_commit"],
        "applied_plan_sha256": policy["source"]["applied_plan_sha256"],
        "review_response_id": policy["independent_review"]["response_id"],
        "rehearsal_id": policy["target"]["rehearsal_id"],
        "evidence_completed_at": policy["cleanup"]["evidence_completed_at"],
        "cleanup_overrun_seconds": policy["cleanup"]["completed_overrun_seconds"],
        "target": dict(policy["target"]),
        "evidence_digests": dict(evidence_digests),
        "claims": dict(CLAIMS),
    }


class AuthenticatedBootstrapIdentityTeardownEvidence:
    """Immutable process-local proof of the reviewed teardown checkpoint."""

    __slots__ = (*PUBLIC_FIELDS, *PRIVATE_FIELDS)

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
    seal: object = _SEAL,
) -> AuthenticatedBootstrapIdentityTeardownEvidence:
    document = _validate_policy(policy)
    before = _trusted_now(clock)
    completed = _parse_utc(
        document["cleanup"]["evidence_completed_at"],
        "bootstrap_identity_teardown_evidence.policy_invalid",
    )
    if before < completed:
        _fail("bootstrap_identity_teardown_evidence.evidence_from_future")
    try:
        grammar.validate_evidence_directory(evidence_directory, document)
    except grammar.EvidenceGrammarFailure as error:
        _fail(str(error))
    after = _trusted_now(clock)
    if after < before:
        _fail("bootstrap_identity_teardown_evidence.clock_regressed")
    evidence_digests = document["evidence"]["files"]
    material = _binding_material(document, evidence_digests)
    value = object.__new__(AuthenticatedBootstrapIdentityTeardownEvidence)
    values = {
        "kind": KIND,
        "teardown_binding_sha256": _digest(_canonical(material)),
        "policy_sha256": f"sha256:{POLICY_SHA256}",
        "source_commit": document["source"]["source_commit"],
        "applied_plan_sha256": document["source"]["applied_plan_sha256"],
        "review_response_id": document["independent_review"]["response_id"],
        "rehearsal_id": document["target"]["rehearsal_id"],
        "authenticated_at": after,
        "evidence_completed_at": completed,
        "cleanup_overrun_seconds": document["cleanup"]["completed_overrun_seconds"],
        **CLAIMS,
        "_target": tuple(document["target"].items()),
        "_evidence_digests": tuple(evidence_digests.items()),
        "_proof_sha256": "",
        "_seal": seal,
    }
    if tuple(values) != (*PUBLIC_FIELDS, *PRIVATE_FIELDS):
        _fail("bootstrap_identity_teardown_evidence.output_invalid")
    for name, item in values.items():
        object.__setattr__(value, name, item)
    return value


def _authentication_shape(
    value: AuthenticatedBootstrapIdentityTeardownEvidence,
    *,
    verify_provenance: Callable[[AuthenticatedBootstrapIdentityTeardownEvidence], bool],
    seal: object,
) -> bool:
    try:
        policy = load_bootstrap_identity_teardown_evidence_policy()
        target = dict(value._target)
        evidence_digests = dict(value._evidence_digests)
        material = _binding_material(policy, evidence_digests)
        public = {
            "kind": KIND,
            "teardown_binding_sha256": _digest(_canonical(material)),
            "policy_sha256": f"sha256:{POLICY_SHA256}",
            "source_commit": policy["source"]["source_commit"],
            "applied_plan_sha256": policy["source"]["applied_plan_sha256"],
            "review_response_id": policy["independent_review"]["response_id"],
            "rehearsal_id": policy["target"]["rehearsal_id"],
            "authenticated_at": value.authenticated_at,
            "evidence_completed_at": _parse_utc(
                policy["cleanup"]["evidence_completed_at"],
                "bootstrap_identity_teardown_evidence.policy_invalid",
            ),
            "cleanup_overrun_seconds": policy["cleanup"]["completed_overrun_seconds"],
            **CLAIMS,
        }
        actual = {name: getattr(value, name) for name in PUBLIC_FIELDS}
        return (
            type(value) is AuthenticatedBootstrapIdentityTeardownEvidence
            and value._seal is seal
            and tuple(public) == PUBLIC_FIELDS
            and target == policy["target"]
            and value._evidence_digests == tuple(policy["evidence"]["files"].items())
            and all(
                type(actual[name]) is type(expected) and actual[name] == expected
                for name, expected in public.items()
            )
            and verify_provenance(value)
        )
    except Exception:
        return False


def _build_provenance() -> tuple[Callable[..., Any], Callable[..., bool]]:
    key = os.urandom(32)
    issued: dict[tuple[int, int], object] = {}
    seal = _SEAL
    shape = _authentication_shape

    def proof(value: AuthenticatedBootstrapIdentityTeardownEvidence) -> str:
        material = _canonical(
            {
                "domain": PROVENANCE_DOMAIN,
                "pid": os.getpid(),
                "object_identity": id(value),
                "teardown_binding_sha256": value.teardown_binding_sha256,
                "authenticated_at": value.authenticated_at.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "seal_identity": id(seal),
            }
        )
        return hmac.new(key, material, hashlib.sha256).hexdigest()

    def verify(value: AuthenticatedBootstrapIdentityTeardownEvidence) -> bool:
        return issued.get((os.getpid(), id(value))) is value and hmac.compare_digest(
            value._proof_sha256, proof(value)
        )

    def is_authenticated(
        value: AuthenticatedBootstrapIdentityTeardownEvidence,
    ) -> bool:
        return shape(value, verify_provenance=verify, seal=seal)

    def authenticate(
        *, evidence_directory: Path
    ) -> AuthenticatedBootstrapIdentityTeardownEvidence:
        policy, _raw = _read_policy()
        value = _validated_candidate(
            policy=policy,
            evidence_directory=evidence_directory,
            clock=_utc_now,
            seal=seal,
        )
        object.__setattr__(value, "_proof_sha256", proof(value))
        issued[os.getpid(), id(value)] = value
        if not is_authenticated(value):
            issued.pop((os.getpid(), id(value)), None)
            _fail("bootstrap_identity_teardown_evidence.output_invalid")
        return value

    return authenticate, is_authenticated


_authenticate, _bound_is_authenticated = _build_provenance()
AuthenticatedBootstrapIdentityTeardownEvidence._is_authenticated = (  # type: ignore[method-assign]
    _bound_is_authenticated
)
del _bound_is_authenticated


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


def _public_projection(
    value: AuthenticatedBootstrapIdentityTeardownEvidence,
) -> dict[str, Any]:
    if not value._is_authenticated():
        _fail("bootstrap_identity_teardown_evidence.output_invalid")
    return {
        name: (
            getattr(value, name).strftime("%Y-%m-%dT%H:%M:%SZ")
            if type(getattr(value, name)) is datetime
            else getattr(value, name)
        )
        for name in PUBLIC_FIELDS
    }


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
