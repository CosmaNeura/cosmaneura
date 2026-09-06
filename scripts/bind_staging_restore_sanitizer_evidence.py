#!/usr/bin/env python3
"""Pure dormant join for sanitizer source state and trusted restore evidence.

The public data boundary accepts only two sealed, in-process authorities.  It
does not read evidence files, accept keys or selectors, contact a provider or
database, or grant sanitizer execution authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

try:
    from scripts import bind_staging_restore_fixed_catalog_source_state as source_state
    from scripts import compose_staging_restore_evidence_binding as composition
    from scripts import staging_restore_safe_submit as safe_submit
    from scripts import staging_restore_sanitizer as sanitizer
    from scripts import staging_restore_sanitizer_evidence_authority as authority
except ModuleNotFoundError:  # Direct `python scripts/...` execution compatibility.
    import bind_staging_restore_fixed_catalog_source_state as source_state
    import compose_staging_restore_evidence_binding as composition
    import staging_restore_safe_submit as safe_submit
    import staging_restore_sanitizer as sanitizer
    import staging_restore_sanitizer_evidence_authority as authority


PUBLIC_FIELDS = authority.PUBLIC_FIELDS
CLAIMS = authority.CLAIMS
CONTEXT_FIELDS = authority.CONTEXT_FIELDS
DIGEST_FIELDS = authority.DIGEST_FIELDS
# Preserve the binder's established API while the executor imports only the
# adapter-free authority leaf.
AuthenticatedSanitizerEvidence = authority.AuthenticatedSanitizerEvidence
SanitizerEvidenceFailure = authority.SanitizerEvidenceFailure
_reauthenticate_at_current_time = authority._reauthenticate_at_current_time  # noqa: SLF001
_proof = authority._proof  # noqa: SLF001

_EVIDENCE_SEAL = authority._EVIDENCE_SEAL  # noqa: SLF001
_FAILURE_CODES = frozenset(
    {
        "sanitizer_evidence.authority_invalid",
        "sanitizer_evidence.clock_invalid",
        "sanitizer_evidence.clock_regressed",
        "sanitizer_evidence.configuration_invalid",
        "sanitizer_evidence.context_invalid",
        "sanitizer_evidence.digest_invalid",
        "sanitizer_evidence.output_invalid",
        "sanitizer_evidence.policy_invalid",
        "sanitizer_evidence.target_expired",
    }
)


def _fail(code: str) -> NoReturn:
    raise SanitizerEvidenceFailure(code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail("sanitizer_evidence.authority_invalid")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _trusted_utc(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail("sanitizer_evidence.clock_invalid")
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
        or value.microsecond != 0
    ):
        _fail("sanitizer_evidence.clock_invalid")
    return value.astimezone(UTC)


def _load_policies() -> tuple[dict[str, Any], Mapping[str, Any], bytes]:
    try:
        sanitizer_policy = sanitizer.load_canonical_policy()
        inventory_policy, _inventory_policy_bytes = (
            source_state._load_inventory_policy()
        )
        sanitizer.validate_policy(sanitizer_policy, inventory_policy)
        composition_policy, composition_sha256, composition_bytes = (
            composition.load_composition_policy_document()
        )
    except Exception:
        _fail("sanitizer_evidence.policy_invalid")
    expected = sanitizer_policy["trusted_evidence_binding"]["upstream_policies"][
        "restore_evidence_composition"
    ]
    if (
        expected
        != {
            "policy_id": composition_policy["policy_id"],
            "sha256": f"sha256:{composition_sha256}",
        }
        or composition_sha256 != sanitizer.RESTORE_EVIDENCE_COMPOSITION_POLICY_SHA256
        or hashlib.sha256(composition_bytes).hexdigest() != composition_sha256
    ):
        _fail("sanitizer_evidence.policy_invalid")
    return sanitizer_policy, inventory_policy, composition_bytes


def _validate_source_state(
    value: source_state.AuthenticatedFixedCatalogSourceState,
    *,
    now: datetime,
) -> tuple[sanitizer.SanitizerTargetBinding, tuple[tuple[str, Any], ...]]:
    if (
        type(value) is not source_state.AuthenticatedFixedCatalogSourceState
        or getattr(value, "_seal", None) is not source_state._SOURCE_STATE_SEAL  # noqa: SLF001
    ):
        _fail("sanitizer_evidence.authority_invalid")
    if now < value.authenticated_at:
        _fail("sanitizer_evidence.clock_regressed")
    if now >= value.target_binding_expires_at:
        _fail("sanitizer_evidence.target_expired")
    target = value._target_binding
    ordered = value._ordered_projection
    if (
        type(target) is not sanitizer.SanitizerTargetBinding
        or target.expires_at != value.target_binding_expires_at
        or type(ordered) is not tuple
    ):
        _fail("sanitizer_evidence.authority_invalid")
    if not value._is_authenticated():
        _fail("sanitizer_evidence.authority_invalid")
    return target, ordered


def _validate_restore_evidence(
    value: safe_submit.RestoreEvidenceBinding,
) -> tuple[dict[str, Any], bytes, datetime, datetime]:
    if (
        type(value) is not safe_submit.RestoreEvidenceBinding
        or not value._is_module_minted_binding()
    ):
        _fail("sanitizer_evidence.authority_invalid")
    try:
        record = safe_submit.SafeRestoreSubmitter._binding_record(value)
        if set(record) != set(sanitizer.receipt_content.EVIDENCE_FIELDS):
            _fail("sanitizer_evidence.authority_invalid")
        observed, expires = sanitizer.receipt_content._validate_evidence(record)
        raw = _canonical(record)
    except SanitizerEvidenceFailure:
        raise
    except Exception:
        _fail("sanitizer_evidence.authority_invalid")
    if len(raw) > sanitizer.MAXIMUM_POLICY_BYTES:
        _fail("sanitizer_evidence.authority_invalid")
    return record, raw, observed, expires


def _validate_join(
    *,
    fixed_catalog_source_state: source_state.AuthenticatedFixedCatalogSourceState,
    restore_evidence_binding: safe_submit.RestoreEvidenceBinding,
    now: datetime,
) -> tuple[bytes, dict[str, Any]]:
    target, ordered_items = _validate_source_state(
        fixed_catalog_source_state,
        now=now,
    )
    record, record_bytes, observed, expires = _validate_restore_evidence(
        restore_evidence_binding
    )
    try:
        built = target._written_receipt._built_receipt
        intent = sanitizer.receipt_content._parse_canonical(
            built.intent_bytes, sanitizer.receipt_content.MAXIMUM_POLICY_BYTES
        )
        receipt = sanitizer.receipt_content._parse_canonical(
            built.receipt_bytes, sanitizer.receipt_content.MAXIMUM_POLICY_BYTES
        )
        intent_evidence = intent["evidence_binding"]
        authorization = receipt["authorization"]
    except Exception:
        _fail("sanitizer_evidence.authority_invalid")
    if (
        type(intent_evidence) is not dict
        or not hmac.compare_digest(record_bytes, _canonical(intent_evidence))
        or authorization.get("evidence_binding_sha256") != _digest(record_bytes)
    ):
        _fail("sanitizer_evidence.digest_invalid")

    ordered = dict(ordered_items)
    if (
        any(record.get(name) != ordered.get(name) for name in CONTEXT_FIELDS)
        or record["inventory_receipt_sha256"]
        != fixed_catalog_source_state.inventory_receipt_sha256
        or observed != target.approved_restore_time
        or any(
            type(record.get(name)) is not str
            or not hmac.compare_digest(record[name], intent_evidence[name])
            for name in DIGEST_FIELDS
        )
    ):
        _fail("sanitizer_evidence.context_invalid")
    public = {
        "rehearsal_id": target.rehearsal_id,
        "controller_receipt_sha256": target.receipt_sha256,
        "evidence_binding_sha256": _digest(record_bytes),
        "evidence_set_sha256": record["evidence_set_sha256"],
        "inventory_receipt_sha256": record["inventory_receipt_sha256"],
        "server_scope_receipt_sha256": record["server_scope_receipt_sha256"],
        "server_scope_authentication_receipt_sha256": record[
            "server_scope_authentication_receipt_sha256"
        ],
        "provider_receipt_sha256": record["provider_receipt_sha256"],
        "attestation_receipt_sha256": record["attestation_receipt_sha256"],
        "evidence_observed_at": observed,
        "evidence_expires_at": expires,
        "target_binding_expires_at": target.expires_at,
        "authenticated_at": now,
    }
    if tuple(public) != PUBLIC_FIELDS:
        _fail("sanitizer_evidence.output_invalid")
    return record_bytes, public


def _mint(
    *,
    fixed_catalog_source_state: source_state.AuthenticatedFixedCatalogSourceState,
    restore_evidence_binding: safe_submit.RestoreEvidenceBinding,
    record_bytes: bytes,
    public: Mapping[str, Any],
    sanitizer_policy: Mapping[str, Any],
    composition_policy_bytes: bytes,
) -> AuthenticatedSanitizerEvidence:
    return authority._mint(  # noqa: SLF001
        fixed_catalog_source_state=fixed_catalog_source_state,
        restore_evidence_binding=restore_evidence_binding,
        record_bytes=record_bytes,
        public=public,
        sanitizer_policy_bytes=sanitizer._canonical_json(sanitizer_policy),  # noqa: SLF001
        composition_policy_bytes=composition_policy_bytes,
    )


class SanitizerEvidenceBinder:
    """Pure binder configured only through a reviewed private UTC clock."""

    __slots__ = ("_clock", "_last")

    def __init__(self) -> None:
        _fail("sanitizer_evidence.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls, *, clock: Callable[[], datetime]
    ) -> SanitizerEvidenceBinder:
        if cls is not SanitizerEvidenceBinder or not callable(clock):
            _fail("sanitizer_evidence.configuration_invalid")
        value = object.__new__(cls)
        value._clock = clock
        value._last = None
        return value

    def _read_clock(self) -> datetime:
        now = _trusted_utc(self._clock)
        if self._last is not None and now < self._last:
            _fail("sanitizer_evidence.clock_regressed")
        self._last = now
        return now

    def bind(
        self,
        *,
        fixed_catalog_source_state: source_state.AuthenticatedFixedCatalogSourceState,
        restore_evidence_binding: safe_submit.RestoreEvidenceBinding,
    ) -> AuthenticatedSanitizerEvidence:
        try:
            if type(self) is not SanitizerEvidenceBinder:
                _fail("sanitizer_evidence.configuration_invalid")
            sanitizer_policy, _inventory_policy, composition_bytes = _load_policies()
            started_at = self._read_clock()
            record_bytes, public = _validate_join(
                fixed_catalog_source_state=fixed_catalog_source_state,
                restore_evidence_binding=restore_evidence_binding,
                now=started_at,
            )
            completed_at = self._read_clock()
            if completed_at != started_at:
                record_bytes, public = _validate_join(
                    fixed_catalog_source_state=fixed_catalog_source_state,
                    restore_evidence_binding=restore_evidence_binding,
                    now=completed_at,
                )
            return _mint(
                fixed_catalog_source_state=fixed_catalog_source_state,
                restore_evidence_binding=restore_evidence_binding,
                record_bytes=record_bytes,
                public=public,
                sanitizer_policy=sanitizer_policy,
                composition_policy_bytes=composition_bytes,
            )
        except Exception as error:
            code = (
                error.args[0]
                if type(error) is SanitizerEvidenceFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
                else "sanitizer_evidence.authority_invalid"
            )
        raise SanitizerEvidenceFailure(code) from None


def build_sanitizer_evidence_binder() -> NoReturn:
    """Refuse public construction while sanitizer execution remains blocked."""

    sanitizer.load_canonical_policy()
    _fail("activation.sanitizer_executor_unimplemented")
