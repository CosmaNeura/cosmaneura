#!/usr/bin/env python3
"""Pure dormant binder for fixed-catalog source-state evidence.

The only accepted authority is the exact in-process sanitizer target binding
plus the exact canonical inventory receipt bytes whose digest was retained in
the authenticated controller intent.  This module has no provider, database,
credential, mutation, or workflow adapter.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

try:
    from scripts import staging_restore_sanitizer as sanitizer
    from scripts import staging_synthetic_inventory as inventory
except ModuleNotFoundError:  # Direct `python scripts/...` execution compatibility.
    import staging_restore_sanitizer as sanitizer
    import staging_synthetic_inventory as inventory

MAXIMUM_INVENTORY_RECEIPT_BYTES = sanitizer.MAXIMUM_INVENTORY_RECEIPT_BYTES
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_RE = re.compile(r"^[1-9][0-9]{0,19}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

FIXED_CATALOG_TABLES = sanitizer.FIXED_CATALOG_TABLE_ORDER
PUBLIC_FIELDS = sanitizer.FIXED_CATALOG_PUBLIC_FIELDS
CLAIMS = sanitizer.FIXED_CATALOG_SOURCE_STATE_CLAIMS

_SOURCE_STATE_SEAL = object()
_FAILURE_CODES = frozenset(
    {
        "fixed_catalog_binding.authority_invalid",
        "fixed_catalog_binding.clock_invalid",
        "fixed_catalog_binding.clock_regressed",
        "fixed_catalog_binding.configuration_invalid",
        "fixed_catalog_binding.context_invalid",
        "fixed_catalog_binding.digest_invalid",
        "fixed_catalog_binding.inventory_invalid",
        "fixed_catalog_binding.output_invalid",
        "fixed_catalog_binding.policy_invalid",
        "fixed_catalog_binding.target_expired",
    }
)


class FixedCatalogSourceStateFailure(RuntimeError):
    """A bounded refusal that never includes receipt or authority contents."""


class _DuplicateKey(ValueError):
    pass


def _fail(code: str) -> NoReturn:
    raise FixedCatalogSourceStateFailure(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError


def _parse_receipt(raw: object) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAXIMUM_INVENTORY_RECEIPT_BYTES:
        _fail("fixed_catalog_binding.inventory_invalid")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateKey, ValueError):
        _fail("fixed_catalog_binding.inventory_invalid")
    if type(document) is not dict or not hmac.compare_digest(raw, _canonical(document)):
        _fail("fixed_catalog_binding.inventory_invalid")
    return document


def _exact_dict(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail(code)
    return value


def _utc(value: object) -> datetime:
    if type(value) is not str or UTC_RE.fullmatch(value) is None:
        _fail("fixed_catalog_binding.inventory_invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _fail("fixed_catalog_binding.inventory_invalid")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail("fixed_catalog_binding.inventory_invalid")
    return parsed


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("fixed_catalog_binding.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        _fail("fixed_catalog_binding.clock_invalid")
    return value.astimezone(UTC)


def _load_inventory_policy() -> tuple[dict[str, Any], bytes]:
    try:
        raw = sanitizer.INVENTORY_POLICY_PATH.read_bytes()
    except OSError:
        _fail("fixed_catalog_binding.policy_invalid")
    if not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), inventory.CANONICAL_POLICY_SHA256
    ):
        _fail("fixed_catalog_binding.policy_invalid")
    try:
        value = json.loads(raw)
        if type(value) is not dict:
            raise ValueError
        inventory.validate_policy(value)
    except (json.JSONDecodeError, ValueError, inventory.InventoryFailure):
        _fail("fixed_catalog_binding.policy_invalid")
    return value, raw


def _expected_receipt_maps(
    inventory_policy: Mapping[str, Any], profile: str
) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    tables = inventory._expected_tables(inventory_policy, profile)  # noqa: SLF001
    counts = {
        "database.non_system_schemas",
        "database.sequences",
        "database.large_objects",
        "database.alembic_version",
        "redis.logical_databases_with_keys",
        "redis.keys",
        *(f"database.schema.{name}" for name in inventory.SCHEMA_DESCRIPTOR_SECTIONS),
        *(f"database.{name}" for name in tables),
        *(f"blob.{name}" for name in inventory_policy["blob"]["containers"]),
    }
    rules = {
        f"database.{table}.{rule['id']}"
        for table, spec in tables.items()
        for rule in spec.get("rules", [])
        if profile in rule.get("profiles", ["pre", "post"])
    }
    identities = {
        table
        for table, spec in tables.items()
        if spec["disposition"] != "required_empty" and spec.get("identity_columns")
    }
    return (
        counts,
        rules,
        set(tables),
        identities,
        set(inventory_policy["blob"]["containers"]),
    )


def _validate_nonnegative_map(
    value: object, expected: set[str], code: str
) -> dict[str, int]:
    result = _exact_dict(value, expected, code)
    if any(type(item) is not int or item < 0 for item in result.values()):
        _fail(code)
    return result


def _validate_hmac_map(value: object, expected: set[str], code: str) -> dict[str, str]:
    result = _exact_dict(value, expected, code)
    if any(
        type(item) is not str or HEX_SHA256_RE.fullmatch(item) is None
        for item in result.values()
    ):
        _fail(code)
    return result


def _validate_inventory_document(
    document: dict[str, Any],
    *,
    inventory_policy: Mapping[str, Any],
    target_binding: sanitizer.SanitizerTargetBinding,
    intent: Mapping[str, Any],
    receipt: Mapping[str, Any],
    now: datetime,
) -> tuple[tuple[tuple[str, Any], ...], dict[str, Any]]:
    evidence_policy = inventory_policy["evidence"]
    _exact_dict(
        document,
        set(evidence_policy["allowed_fields"]),
        "fixed_catalog_binding.inventory_invalid",
    )
    provenance = _exact_dict(
        document["provenance"],
        {
            "repository",
            "protected_ref",
            "workflow_path",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "event_name",
        },
        "fixed_catalog_binding.inventory_invalid",
    )
    signer = _exact_dict(
        document["signer_claims"],
        {
            "issuer",
            "subject",
            "repository",
            "ref",
            "sha",
            "workflow_ref",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "actor",
            "triggering_actor",
        },
        "fixed_catalog_binding.inventory_invalid",
    )
    source_resource = _exact_dict(
        document["source_resource"],
        {
            "blob_account_id",
            "cloud",
            "database_name",
            "postgres_server_id",
            "redis_cache_id",
        },
        "fixed_catalog_binding.inventory_invalid",
    )
    profile = "pre"
    profile_policy = inventory_policy["profiles"][profile]
    count_keys, rule_keys, state_keys, identity_keys, blob_keys = (
        _expected_receipt_maps(inventory_policy, profile)
    )
    counts = _validate_nonnegative_map(
        document["counts"], count_keys, "fixed_catalog_binding.inventory_invalid"
    )
    rules = _validate_nonnegative_map(
        document["rule_violation_counts"],
        rule_keys,
        "fixed_catalog_binding.inventory_invalid",
    )
    state_hmacs = _validate_hmac_map(
        document["state_hmacs"],
        state_keys,
        "fixed_catalog_binding.inventory_invalid",
    )
    _validate_hmac_map(
        document["identity_hmacs"],
        identity_keys,
        "fixed_catalog_binding.inventory_invalid",
    )
    _validate_hmac_map(
        document["blob_state_hmacs"],
        blob_keys,
        "fixed_catalog_binding.inventory_invalid",
    )
    observed_at = _utc(document["observed_at"])
    expires_at = _utc(document["expires_at"])
    trusted = evidence_policy["trusted_context"]
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != 2
        or document["policy_id"] != inventory_policy["policy_id"]
        or document["policy_sha256"] != inventory.CANONICAL_POLICY_SHA256
        or type(document["approved_state_sha256"]) is not str
        or HEX_SHA256_RE.fullmatch(document["approved_state_sha256"]) is None
        or document["hmac_key_version"] != inventory.SUPPORTED_HMAC_KEY_VERSION
        or source_resource != evidence_policy["source_resource"]
        or document["phase"] != profile
        or type(document["source_sha"]) is not str
        or SHA_RE.fullmatch(document["source_sha"]) is None
        or type(document["candidate_sha"]) is not str
        or SHA_RE.fullmatch(document["candidate_sha"]) is None
        or document["schema_descriptor_sha256"]
        != profile_policy["schema_descriptor"]["sha256"]
        or type(document["catalog_hmac"]) is not str
        or HEX_SHA256_RE.fullmatch(document["catalog_hmac"]) is None
        or type(document["redis_state_hmac"]) is not str
        or HEX_SHA256_RE.fullmatch(document["redis_state_hmac"]) is None
        or type(document["receipt_sha256"]) is not str
        or HEX_SHA256_RE.fullmatch(document["receipt_sha256"]) is None
        or document["errors"] != []
        or document["outcome"] != "pass"
        or any(value != 0 for value in rules.values())
        or expires_at <= observed_at
        or expires_at - observed_at
        > timedelta(seconds=evidence_policy["maximum_lifetime_seconds"])
        or observed_at > now
        or counts["database.non_system_schemas"] != 1
        or counts["database.sequences"] != 0
        or counts["database.large_objects"] != 0
        or counts["database.alembic_version"] != 1
        or counts["redis.logical_databases_with_keys"] != 0
        or counts["redis.keys"] != 0
        or any(
            counts[f"database.schema.{name}"] != expected
            for name, expected in profile_policy["schema_descriptor"]["counts"].items()
        )
        or any(
            counts[f"database.{name}"] != 0
            for name, spec in inventory._expected_tables(  # noqa: SLF001
                inventory_policy, profile
            ).items()
            if spec["disposition"] == "required_empty"
        )
        or any(
            counts[f"blob.{name}"] != 0
            for name, disposition in inventory_policy["blob"]["containers"].items()
            if disposition == "required_empty"
        )
    ):
        _fail("fixed_catalog_binding.inventory_invalid")

    if (
        provenance["repository"] != trusted["repository"]
        or provenance["protected_ref"] != trusted["protected_ref"]
        or provenance["workflow_path"] != trusted["workflow_path"]
        or provenance["event_name"] != "workflow_dispatch"
        or type(provenance["workflow_sha"]) is not str
        or SHA_RE.fullmatch(provenance["workflow_sha"]) is None
        or type(provenance["run_id"]) is not str
        or RUN_RE.fullmatch(provenance["run_id"]) is None
        or type(provenance["run_attempt"]) is not str
        or RUN_RE.fullmatch(provenance["run_attempt"]) is None
        or signer["issuer"] != trusted["issuer"]
        or signer["subject"] != trusted["subject"]
        or signer["repository"] != provenance["repository"]
        or signer["ref"] != provenance["protected_ref"]
        or signer["sha"] != provenance["workflow_sha"]
        or signer["workflow_sha"] != provenance["workflow_sha"]
        or signer["workflow_ref"]
        != (
            f"{provenance['repository']}/{provenance['workflow_path']}"
            f"@{provenance['protected_ref']}"
        )
        or signer["run_id"] != provenance["run_id"]
        or signer["run_attempt"] != provenance["run_attempt"]
        or signer["actor"] != trusted["founder_actor"]
        or signer["triggering_actor"] != trusted["founder_actor"]
    ):
        _fail("fixed_catalog_binding.inventory_invalid")

    evidence = _exact_dict(
        intent["evidence_binding"],
        set(sanitizer.receipt_content.EVIDENCE_FIELDS),
        "fixed_catalog_binding.authority_invalid",
    )
    context = {
        "source_sha": document["source_sha"],
        "candidate_sha": document["candidate_sha"],
        "source_server_resource_id": source_resource["postgres_server_id"],
        "source_database_name": source_resource["database_name"],
        "approved_restore_time": document["observed_at"],
        "workflow_sha": provenance["workflow_sha"],
        "run_id": provenance["run_id"],
        "run_attempt": provenance["run_attempt"],
        "repository": provenance["repository"],
        "protected_ref": provenance["protected_ref"],
        "workflow_path": provenance["workflow_path"],
        "signer_issuer": signer["issuer"],
        "signer_subject": signer["subject"],
    }
    target_context = {
        "source_sha": target_binding.source_sha,
        "candidate_sha": target_binding.candidate_sha,
        "source_server_resource_id": receipt["source"]["source_server_resource_id"],
        "source_database_name": target_binding.database_name,
        "approved_restore_time": target_binding.approved_restore_time.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "workflow_sha": target_binding.workflow_sha,
        "run_id": target_binding.run_id,
        "run_attempt": target_binding.run_attempt,
        "repository": receipt["repository"],
        "protected_ref": evidence["protected_ref"],
        "workflow_path": evidence["workflow_path"],
        "signer_issuer": evidence["signer_issuer"],
        "signer_subject": evidence["signer_subject"],
    }
    if (
        context != target_context
        or any(
            context[name] != evidence[name]
            for name in sanitizer.FIXED_CATALOG_CONTEXT_FIELDS
        )
        or intent["source_server_resource_id"] != context["source_server_resource_id"]
        or intent["restore_time"] != context["approved_restore_time"]
        or intent["repository"] != context["repository"]
        or intent["workflow_sha"] != context["workflow_sha"]
        or intent["run_id"] != context["run_id"]
        or intent["run_attempt"] != context["run_attempt"]
    ):
        _fail("fixed_catalog_binding.context_invalid")

    unsigned = dict(document)
    del unsigned["receipt_sha256"]
    calculated = hashlib.sha256(_canonical(unsigned)).hexdigest()
    expected_digest = evidence["inventory_receipt_sha256"]
    if (
        type(expected_digest) is not str
        or not hmac.compare_digest(calculated, document["receipt_sha256"])
        or not hmac.compare_digest(f"sha256:{calculated}", expected_digest)
    ):
        _fail("fixed_catalog_binding.digest_invalid")

    fixed_catalog = tuple(
        (
            table,
            counts[f"database.{table}"],
            state_hmacs[table],
        )
        for table in FIXED_CATALOG_TABLES
    )
    ordered_projection: tuple[tuple[str, Any], ...] = (
        ("rehearsal_id", target_binding.rehearsal_id),
        ("target_binding_receipt_sha256", target_binding.receipt_sha256),
        *(tuple(context.items())),
        ("inventory_policy_sha256", f"sha256:{document['policy_sha256']}"),
        ("approved_state_sha256", f"sha256:{document['approved_state_sha256']}"),
        ("hmac_key_version", document["hmac_key_version"]),
        ("source_profile", profile),
        ("source_revision", profile_policy["revision"]),
        ("inventory_observed_at", document["observed_at"]),
        ("inventory_expires_at", document["expires_at"]),
        ("fixed_catalog", fixed_catalog),
    )
    fixed_projection_sha256 = (
        "sha256:"
        + hashlib.sha256(
            _canonical(
                [
                    {"table": table, "count": count, "state_hmac": state_hmac}
                    for table, count, state_hmac in fixed_catalog
                ]
            )
        ).hexdigest()
    )
    public = {
        "inventory_receipt_sha256": expected_digest,
        "inventory_policy_sha256": f"sha256:{document['policy_sha256']}",
        "approved_state_sha256": f"sha256:{document['approved_state_sha256']}",
        "fixed_catalog_projection_sha256": fixed_projection_sha256,
        "hmac_key_version": document["hmac_key_version"],
        "source_profile": profile,
        "source_revision": profile_policy["revision"],
        "inventory_observed_at": observed_at,
        "inventory_expires_at": expires_at,
        "target_binding_expires_at": target_binding.expires_at,
        "authenticated_at": now,
    }
    return ordered_projection, public


def _authenticate(
    *,
    target_binding: sanitizer.SanitizerTargetBinding,
    inventory_receipt_bytes: bytes,
    sanitizer_policy: Mapping[str, Any],
    inventory_policy: Mapping[str, Any],
    now: datetime,
) -> tuple[tuple[tuple[str, Any], ...], dict[str, Any]]:
    if (
        type(target_binding) is not sanitizer.SanitizerTargetBinding
        or not target_binding._is_bound()
        or now < target_binding.bound_at
    ):
        _fail("fixed_catalog_binding.authority_invalid")
    try:
        expected_target = sanitizer._binding_projection(  # noqa: SLF001
            written_receipt=target_binding._written_receipt,
            policy=sanitizer_policy,
            now=now,
        )
    except sanitizer.SanitizerFailure as error:
        if error.args == ("sanitizer_binding.expired",):
            _fail("fixed_catalog_binding.target_expired")
        _fail("fixed_catalog_binding.authority_invalid")
    if any(
        getattr(target_binding, name) != value
        for name, value in expected_target.items()
        if name != "bound_at"
    ):
        _fail("fixed_catalog_binding.authority_invalid")
    built = target_binding._written_receipt._built_receipt
    intent = sanitizer.receipt_content._parse_canonical(
        built.intent_bytes, sanitizer.receipt_content.MAXIMUM_POLICY_BYTES
    )
    receipt = sanitizer.receipt_content._parse_canonical(
        built.receipt_bytes, sanitizer.receipt_content.MAXIMUM_POLICY_BYTES
    )
    document = _parse_receipt(inventory_receipt_bytes)
    return _validate_inventory_document(
        document,
        inventory_policy=inventory_policy,
        target_binding=target_binding,
        intent=intent,
        receipt=receipt,
        now=now,
    )


def _proof(
    ordered_projection: tuple[tuple[str, Any], ...], public: Mapping[str, Any]
) -> str:
    serializable_public = dict(public)
    for name in (
        "inventory_observed_at",
        "inventory_expires_at",
        "target_binding_expires_at",
        "authenticated_at",
    ):
        value = serializable_public[name]
        if type(value) is not datetime:
            _fail("fixed_catalog_binding.output_invalid")
        serializable_public[name] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return hashlib.sha256(
        _canonical(
            {
                "ordered_projection": ordered_projection,
                "public": serializable_public,
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedFixedCatalogSourceState:
    """Sealed non-serializable fixed-catalog source-state authority."""

    inventory_receipt_sha256: str
    inventory_policy_sha256: str
    approved_state_sha256: str
    fixed_catalog_projection_sha256: str
    hmac_key_version: str
    source_profile: str
    source_revision: str
    inventory_observed_at: datetime
    inventory_expires_at: datetime
    target_binding_expires_at: datetime
    authenticated_at: datetime
    controller_receipt_v2_target_binding_authenticated: bool
    fixed_catalog_source_state_authenticated: bool
    inventory_receipt_digest_authenticated: bool
    database_connection_authority: bool
    database_mutation_authority: bool
    executable: bool
    live_provider_calls_enabled: bool
    post_projection_authenticated: bool
    provider_calls_permitted: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    sanitizer_authority: bool
    server_scope_authenticated: bool
    workflow_integrated: bool
    _target_binding: sanitizer.SanitizerTargetBinding = dataclass_field(repr=False)
    _inventory_receipt_bytes: bytes = dataclass_field(repr=False)
    _ordered_projection: tuple[tuple[str, Any], ...] = dataclass_field(repr=False)
    _sanitizer_policy_bytes: bytes = dataclass_field(repr=False)
    _inventory_policy_bytes: bytes = dataclass_field(repr=False)
    _proof_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False)

    def __init__(self) -> None:
        _fail("fixed_catalog_binding.output_invalid")

    def __reduce__(self) -> NoReturn:
        _fail("fixed_catalog_binding.output_invalid")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        _fail("fixed_catalog_binding.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            sanitizer_policy = sanitizer.load_canonical_policy()
            inventory_policy, inventory_policy_bytes = _load_inventory_policy()
            sanitizer.validate_policy(sanitizer_policy, inventory_policy)
            ordered, public = _authenticate(
                target_binding=self._target_binding,
                inventory_receipt_bytes=self._inventory_receipt_bytes,
                sanitizer_policy=sanitizer_policy,
                inventory_policy=inventory_policy,
                now=self.authenticated_at,
            )
            return (
                type(self) is AuthenticatedFixedCatalogSourceState
                and self._seal is _SOURCE_STATE_SEAL
                and self._sanitizer_policy_bytes
                == sanitizer._canonical_json(  # noqa: SLF001
                    sanitizer_policy
                )
                and self._inventory_policy_bytes == inventory_policy_bytes
                and self._ordered_projection == ordered
                and all(getattr(self, name) == value for name, value in public.items())
                and all(getattr(self, name) is value for name, value in CLAIMS.items())
                and self._proof_sha256 == _proof(ordered, public)
            )
        except Exception:
            return False


def _mint(
    *,
    target_binding: sanitizer.SanitizerTargetBinding,
    inventory_receipt_bytes: bytes,
    ordered_projection: tuple[tuple[str, Any], ...],
    public: Mapping[str, Any],
    sanitizer_policy: Mapping[str, Any],
    inventory_policy_bytes: bytes,
) -> AuthenticatedFixedCatalogSourceState:
    value = object.__new__(AuthenticatedFixedCatalogSourceState)
    for name, item in {**public, **CLAIMS}.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_target_binding", target_binding)
    object.__setattr__(value, "_inventory_receipt_bytes", inventory_receipt_bytes)
    object.__setattr__(value, "_ordered_projection", ordered_projection)
    object.__setattr__(
        value,
        "_sanitizer_policy_bytes",
        sanitizer._canonical_json(sanitizer_policy),  # noqa: SLF001
    )
    object.__setattr__(value, "_inventory_policy_bytes", inventory_policy_bytes)
    object.__setattr__(value, "_proof_sha256", _proof(ordered_projection, public))
    object.__setattr__(value, "_seal", _SOURCE_STATE_SEAL)
    if (
        type(value) is not AuthenticatedFixedCatalogSourceState
        or value._seal is not _SOURCE_STATE_SEAL
        or value._ordered_projection != ordered_projection
        or any(getattr(value, name) != item for name, item in public.items())
        or any(getattr(value, name) is not item for name, item in CLAIMS.items())
        or value._proof_sha256 != _proof(ordered_projection, public)
    ):
        _fail("fixed_catalog_binding.output_invalid")
    return value


class FixedCatalogSourceStateBinder:
    """Pure binder configured only through a reviewed private UTC clock."""

    __slots__ = ("_clock", "_last")

    def __init__(self) -> None:
        _fail("fixed_catalog_binding.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> FixedCatalogSourceStateBinder:
        if cls is not FixedCatalogSourceStateBinder or not callable(clock):
            _fail("fixed_catalog_binding.configuration_invalid")
        value = object.__new__(cls)
        value._clock = clock
        value._last = None
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("fixed_catalog_binding.clock_regressed")
        self._last = now
        return now

    def bind(
        self,
        *,
        target_binding: sanitizer.SanitizerTargetBinding,
        inventory_receipt_bytes: bytes,
    ) -> AuthenticatedFixedCatalogSourceState:
        try:
            if type(self) is not FixedCatalogSourceStateBinder:
                _fail("fixed_catalog_binding.configuration_invalid")
            sanitizer_policy = sanitizer.load_canonical_policy()
            inventory_policy, inventory_policy_bytes = _load_inventory_policy()
            sanitizer.validate_policy(sanitizer_policy, inventory_policy)
            started_at = self._read_clock()
            ordered, public = _authenticate(
                target_binding=target_binding,
                inventory_receipt_bytes=inventory_receipt_bytes,
                sanitizer_policy=sanitizer_policy,
                inventory_policy=inventory_policy,
                now=started_at,
            )
            completed_at = self._read_clock()
            if completed_at != started_at:
                ordered, public = _authenticate(
                    target_binding=target_binding,
                    inventory_receipt_bytes=inventory_receipt_bytes,
                    sanitizer_policy=sanitizer_policy,
                    inventory_policy=inventory_policy,
                    now=completed_at,
                )
            return _mint(
                target_binding=target_binding,
                inventory_receipt_bytes=inventory_receipt_bytes,
                ordered_projection=ordered,
                public=public,
                sanitizer_policy=sanitizer_policy,
                inventory_policy_bytes=inventory_policy_bytes,
            )
        except Exception as error:
            code = (
                error.args[0]
                if type(error) is FixedCatalogSourceStateFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
                else "fixed_catalog_binding.authority_invalid"
            )
        raise FixedCatalogSourceStateFailure(code) from None


def build_fixed_catalog_source_state_binder() -> NoReturn:
    """Refuse public construction while sanitizer activation remains blocked."""

    sanitizer.load_canonical_policy()
    _fail("activation.sanitizer_executor_unimplemented")
