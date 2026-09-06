#!/usr/bin/env python3
"""Adapter-free sealed authority consumed by the sanitizer executor preflight."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

MAXIMUM_RECORD_BYTES = 64 * 1024
COMPOSITION_POLICY_SHA256 = (
    "ba97547aa95686af448c169949379bb8ed1c20d4683adc3b1c6a70114ca6c1c3"
)
PUBLIC_FIELDS = (
    "rehearsal_id",
    "controller_receipt_sha256",
    "evidence_binding_sha256",
    "evidence_set_sha256",
    "inventory_receipt_sha256",
    "server_scope_receipt_sha256",
    "server_scope_authentication_receipt_sha256",
    "provider_receipt_sha256",
    "attestation_receipt_sha256",
    "evidence_observed_at",
    "evidence_expires_at",
    "target_binding_expires_at",
    "authenticated_at",
)
CLAIMS = {
    "controller_receipt_v2_target_binding_authenticated": True,
    "fixed_catalog_source_state_authenticated": True,
    "inventory_receipt_digest_authenticated": True,
    "restore_evidence_binding_authenticated": True,
    "server_scope_authenticated": True,
    "database_connection_authority": False,
    "database_mutation_authority": False,
    "executable": False,
    "live_provider_calls_enabled": False,
    "post_projection_authenticated": False,
    "provider_calls_permitted": False,
    "provider_facts_authenticated": False,
    "runtime_authority": False,
    "sanitizer_authority": False,
    "workflow_integrated": False,
}
CONTEXT_FIELDS = (
    "source_sha",
    "candidate_sha",
    "source_server_resource_id",
    "source_database_name",
    "approved_restore_time",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "repository",
    "protected_ref",
    "workflow_path",
    "signer_issuer",
    "signer_subject",
)
DIGEST_FIELDS = (
    "evidence_manifest_policy_sha256",
    "manifest_file_sha256",
    "bundle_index_sha256",
    "evidence_set_sha256",
    "candidate_receipt_sha256",
    "inventory_receipt_sha256",
    "quiescence_receipt_sha256",
    "server_scope_receipt_sha256",
    "attestation_receipt_sha256",
    "provider_receipt_sha256",
    "server_scope_authentication_receipt_sha256",
)
RESTORE_EVIDENCE_FIELDS = (
    "schema_version",
    "binding_policy_id",
    "evidence_manifest_policy_id",
    "evidence_manifest_policy_sha256",
    "manifest_id",
    "manifest_file_sha256",
    "bundle_index_sha256",
    "evidence_set_sha256",
    "source_sha",
    "candidate_sha",
    "source_server_resource_id",
    "source_database_name",
    "approved_restore_time",
    "observed_at",
    "expires_at",
    "candidate_approval_pr_number",
    "candidate_protected_master_sha",
    "candidate_schema_revision",
    "candidate_receipt_sha256",
    "inventory_receipt_sha256",
    "quiescence_receipt_sha256",
    "server_scope_receipt_sha256",
    "repository",
    "protected_ref",
    "workflow_path",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "event_name",
    "signer_issuer",
    "signer_subject",
    "attestation_receipt_sha256",
    "provider_receipt_sha256",
    "server_scope_authentication_receipt_sha256",
)
_RESTORE_TIME_FIELDS = frozenset({"approved_restore_time", "observed_at", "expires_at"})
_RESTORE_EVIDENCE_BINDING_SEAL = object()
_EVIDENCE_SEAL = object()


class SanitizerEvidenceFailure(RuntimeError):
    """A bounded refusal that never includes retained authority contents."""


def _fail(code: str) -> NoReturn:
    raise SanitizerEvidenceFailure(code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("sanitizer_evidence.authority_invalid")


def _parse_canonical(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= MAXIMUM_RECORD_BYTES:
        _fail("sanitizer_evidence.authority_invalid")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                _fail("sanitizer_evidence.authority_invalid")
            result[name] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: _fail("sanitizer_evidence.authority_invalid"),
            parse_float=lambda _value: _fail("sanitizer_evidence.authority_invalid"),
        )
    except SanitizerEvidenceFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise SanitizerEvidenceFailure("sanitizer_evidence.authority_invalid") from None
    if type(value) is not dict or not hmac.compare_digest(raw, _canonical(value)):
        _fail("sanitizer_evidence.authority_invalid")
    return value


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _iso(value: object) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        _fail("sanitizer_evidence.authority_invalid")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class RestoreEvidenceBinding:
    """Adapter-free final evidence value minted by the composition boundary."""

    schema_version: int
    binding_policy_id: str
    evidence_manifest_policy_id: str
    evidence_manifest_policy_sha256: str
    manifest_id: str
    manifest_file_sha256: str
    bundle_index_sha256: str
    evidence_set_sha256: str
    source_sha: str
    candidate_sha: str
    source_server_resource_id: str
    source_database_name: str
    approved_restore_time: datetime
    observed_at: datetime
    expires_at: datetime
    candidate_approval_pr_number: int
    candidate_protected_master_sha: str
    candidate_schema_revision: str
    candidate_receipt_sha256: str
    inventory_receipt_sha256: str
    quiescence_receipt_sha256: str
    server_scope_receipt_sha256: str
    repository: str
    protected_ref: str
    workflow_path: str
    workflow_sha: str
    run_id: str
    run_attempt: str
    event_name: str
    signer_issuer: str
    signer_subject: str
    attestation_receipt_sha256: str
    provider_receipt_sha256: str
    server_scope_authentication_receipt_sha256: str
    _binding_seal: object = dataclass_field(repr=False, compare=False)

    def _is_module_minted_binding(self) -> bool:
        return (
            type(self) is RestoreEvidenceBinding
            and getattr(self, "_binding_seal", None) is _RESTORE_EVIDENCE_BINDING_SEAL
        )


def _restore_record(value: RestoreEvidenceBinding) -> dict[str, Any]:
    if (
        type(value) is not RestoreEvidenceBinding
        or not RestoreEvidenceBinding._is_module_minted_binding(value)
    ):
        _fail("sanitizer_evidence.authority_invalid")
    result: dict[str, Any] = {}
    for name in RESTORE_EVIDENCE_FIELDS:
        item = getattr(value, name)
        result[name] = _iso(item) if name in _RESTORE_TIME_FIELDS else item
    return result


def _proof(record_bytes: bytes, public: Mapping[str, Any]) -> str:
    serializable = dict(public)
    for name in (
        "evidence_observed_at",
        "evidence_expires_at",
        "target_binding_expires_at",
        "authenticated_at",
    ):
        serializable[name] = _iso(serializable[name])
    return hashlib.sha256(
        _canonical(
            {
                "evidence_record_sha256": _digest(record_bytes),
                "public": serializable,
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedSanitizerEvidence:
    """Sealed evidence authority with no connection or mutation capability."""

    rehearsal_id: str
    controller_receipt_sha256: str
    evidence_binding_sha256: str
    evidence_set_sha256: str
    inventory_receipt_sha256: str
    server_scope_receipt_sha256: str
    server_scope_authentication_receipt_sha256: str
    provider_receipt_sha256: str
    attestation_receipt_sha256: str
    evidence_observed_at: datetime
    evidence_expires_at: datetime
    target_binding_expires_at: datetime
    authenticated_at: datetime
    controller_receipt_v2_target_binding_authenticated: bool
    fixed_catalog_source_state_authenticated: bool
    inventory_receipt_digest_authenticated: bool
    restore_evidence_binding_authenticated: bool
    server_scope_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    executable: bool
    live_provider_calls_enabled: bool
    post_projection_authenticated: bool
    provider_calls_permitted: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    sanitizer_authority: bool
    workflow_integrated: bool
    _fixed_catalog_source_state: Any = dataclass_field(repr=False)
    _fixed_catalog_source_state_type: type[Any] = dataclass_field(repr=False)
    _fixed_catalog_source_state_seal: object = dataclass_field(repr=False)
    _restore_evidence_binding: RestoreEvidenceBinding = dataclass_field(repr=False)
    _evidence_record_bytes: bytes = dataclass_field(repr=False)
    _sanitizer_policy_bytes: bytes = dataclass_field(repr=False)
    _composition_policy_bytes: bytes = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False)

    def __init__(self) -> None:
        _fail("sanitizer_evidence.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("sanitizer_evidence.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("sanitizer_evidence.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            _reauthenticate_at_current_time(self, now=self.authenticated_at)
            return True
        except Exception:
            return False


def _validate_at_current_time(
    value: AuthenticatedSanitizerEvidence,
    *,
    now: datetime,
    reauthenticate_source: bool,
) -> None:
    """Validate an exact evidence envelope, optionally reauthenticating ancestry."""

    if (
        type(now) is not datetime
        or now.tzinfo is None
        or now.utcoffset() != timedelta(0)
        or now.microsecond != 0
    ):
        _fail("sanitizer_evidence.clock_invalid")
    now = now.astimezone(UTC)
    if (
        type(value) is not AuthenticatedSanitizerEvidence
        or getattr(value, "_seal", None) is not _EVIDENCE_SEAL
        or type(getattr(value, "authenticated_at", None)) is not datetime
        or value.authenticated_at.tzinfo is None
        or value.authenticated_at.utcoffset() != timedelta(0)
        or value.authenticated_at.microsecond != 0
    ):
        _fail("sanitizer_evidence.authority_invalid")
    if now < value.authenticated_at:
        _fail("sanitizer_evidence.clock_regressed")

    source = value._fixed_catalog_source_state
    source_type = value._fixed_catalog_source_state_type
    source_seal = value._fixed_catalog_source_state_seal
    if (
        type(source_type) is not type
        or type(source) is not source_type
        or getattr(source, "_seal", None) is not source_seal
    ):
        _fail("sanitizer_evidence.authority_invalid")
    if reauthenticate_source:
        try:
            reauthenticate = source_type.__dict__["_reauthenticate_at_current_time"]
            if not callable(reauthenticate):
                _fail("sanitizer_evidence.authority_invalid")
            reauthenticate(source, now=now)
        except Exception as error:
            if error.args == ("fixed_catalog_binding.target_expired",):
                _fail("sanitizer_evidence.target_expired")
            if error.args == ("fixed_catalog_binding.clock_regressed",):
                _fail("sanitizer_evidence.clock_regressed")
            _fail("sanitizer_evidence.authority_invalid")

    try:
        target = source._target_binding
        ordered = dict(source._ordered_projection)
        restore = value._restore_evidence_binding
        restore_record = _restore_record(restore)
        record = _parse_canonical(value._evidence_record_bytes)
        built = target._written_receipt._built_receipt
        intent = _parse_canonical(built.intent_bytes)
        receipt = _parse_canonical(built.receipt_bytes)
        intent_evidence = intent["evidence_binding"]
        authorization = receipt["authorization"]
    except SanitizerEvidenceFailure:
        raise
    except Exception:
        _fail("sanitizer_evidence.authority_invalid")
    if (
        set(record) != set(RESTORE_EVIDENCE_FIELDS)
        or not hmac.compare_digest(
            value._evidence_record_bytes, _canonical(restore_record)
        )
        or type(intent_evidence) is not dict
        or not hmac.compare_digest(value._evidence_record_bytes, _canonical(record))
        or not hmac.compare_digest(
            value._evidence_record_bytes, _canonical(intent_evidence)
        )
        or authorization.get("evidence_binding_sha256")
        != _digest(value._evidence_record_bytes)
        or any(record.get(name) != ordered.get(name) for name in CONTEXT_FIELDS)
        or record["inventory_receipt_sha256"] != source.inventory_receipt_sha256
        or record["approved_restore_time"] != _iso(target.approved_restore_time)
        or any(
            type(record.get(name)) is not str
            or not hmac.compare_digest(record[name], intent_evidence[name])
            for name in DIGEST_FIELDS
        )
    ):
        _fail("sanitizer_evidence.authority_invalid")

    public = {
        "rehearsal_id": target.rehearsal_id,
        "controller_receipt_sha256": target.receipt_sha256,
        "evidence_binding_sha256": _digest(value._evidence_record_bytes),
        "evidence_set_sha256": record["evidence_set_sha256"],
        "inventory_receipt_sha256": record["inventory_receipt_sha256"],
        "server_scope_receipt_sha256": record["server_scope_receipt_sha256"],
        "server_scope_authentication_receipt_sha256": record[
            "server_scope_authentication_receipt_sha256"
        ],
        "provider_receipt_sha256": record["provider_receipt_sha256"],
        "attestation_receipt_sha256": record["attestation_receipt_sha256"],
        "evidence_observed_at": restore.observed_at,
        "evidence_expires_at": restore.expires_at,
        "target_binding_expires_at": target.expires_at,
        "authenticated_at": value.authenticated_at,
    }
    if (
        tuple(public) != PUBLIC_FIELDS
        or value._sanitizer_policy_bytes != source._sanitizer_policy_bytes
        or hashlib.sha256(value._composition_policy_bytes).hexdigest()
        != COMPOSITION_POLICY_SHA256
        or any(getattr(value, name) != item for name, item in public.items())
        or any(getattr(value, name) is not item for name, item in CLAIMS.items())
        or value._proof_sha256 != _proof(value._evidence_record_bytes, public)
    ):
        _fail("sanitizer_evidence.authority_invalid")


def _reauthenticate_at_current_time(
    value: AuthenticatedSanitizerEvidence,
    *,
    now: datetime,
) -> None:
    """Reauthenticate an exact evidence authority and all ancestry at ``now``."""

    _validate_at_current_time(value, now=now, reauthenticate_source=True)


def _validate_retained_envelope_at_current_time(
    value: AuthenticatedSanitizerEvidence,
    *,
    now: datetime,
) -> None:
    """Validate this sealed envelope without dispatching into retained ancestry."""

    _validate_at_current_time(value, now=now, reauthenticate_source=False)


def _mint(
    *,
    fixed_catalog_source_state: Any,
    restore_evidence_binding: RestoreEvidenceBinding,
    record_bytes: bytes,
    public: Mapping[str, Any],
    sanitizer_policy_bytes: bytes,
    composition_policy_bytes: bytes,
) -> AuthenticatedSanitizerEvidence:
    value = object.__new__(AuthenticatedSanitizerEvidence)
    for name, item in {**public, **CLAIMS}.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_fixed_catalog_source_state", fixed_catalog_source_state)
    object.__setattr__(
        value, "_fixed_catalog_source_state_type", type(fixed_catalog_source_state)
    )
    object.__setattr__(
        value,
        "_fixed_catalog_source_state_seal",
        fixed_catalog_source_state._seal,
    )
    object.__setattr__(value, "_restore_evidence_binding", restore_evidence_binding)
    object.__setattr__(value, "_evidence_record_bytes", record_bytes)
    object.__setattr__(value, "_sanitizer_policy_bytes", sanitizer_policy_bytes)
    object.__setattr__(value, "_composition_policy_bytes", composition_policy_bytes)
    object.__setattr__(value, "_proof_sha256", _proof(record_bytes, public))
    object.__setattr__(value, "_seal", _EVIDENCE_SEAL)
    if (
        type(value) is not AuthenticatedSanitizerEvidence
        or value._seal is not _EVIDENCE_SEAL
        or value._fixed_catalog_source_state is not fixed_catalog_source_state
        or value._fixed_catalog_source_state_type
        is not type(fixed_catalog_source_state)
        or value._fixed_catalog_source_state_seal
        is not fixed_catalog_source_state._seal
        or value._restore_evidence_binding is not restore_evidence_binding
        or value._evidence_record_bytes != record_bytes
        or any(getattr(value, name) != item for name, item in public.items())
        or any(getattr(value, name) is not item for name, item in CLAIMS.items())
        or value._proof_sha256 != _proof(record_bytes, public)
    ):
        _fail("sanitizer_evidence.output_invalid")
    return value
