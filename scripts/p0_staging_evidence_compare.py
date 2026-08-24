#!/usr/bin/env python3
"""Fail closed unless a P0 staging rollout changed only reviewed P0 state.

The command authenticates both aggregate evidence snapshots with the same
owner-only HMAC key used during capture. It compares the business-state
projections that the rollout must not change and requires an owner-only JSON
file containing the exact aggregate counts expected in the new P0 tables.
Neither snapshots nor counts are emitted.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import p0_staging_evidence as evidence

MAXIMUM_JSON_BYTES = 1024 * 1024


class ComparisonFailure(RuntimeError):
    """A comparison failure whose private details must not be emitted."""


UNCHANGED_PATHS: tuple[tuple[str, ...], ...] = (
    ("sections", "baa", "acceptances", "count"),
    ("sections", "baa", "acceptances", "state_hmac_sha256"),
    ("sections", "baa", "versions", "count"),
    ("sections", "baa", "versions", "state_hmac_sha256"),
    ("sections", "doctor_account_state", "count"),
    ("sections", "doctor_account_state", "active_count"),
    ("sections", "doctor_account_state", "deleted_count"),
    ("sections", "doctor_account_state", "legacy_admin_role_count"),
    ("sections", "doctor_account_state", "onboarding_completed_count"),
    ("sections", "doctor_account_state", "onboarding_pending_count"),
    (
        "sections",
        "doctor_account_state",
        "onboarding_state_hmac_sha256",
    ),
    (
        "sections",
        "doctor_account_state",
        "identity_authority_state_hmac_sha256",
    ),
    ("sections", "practice_business_state", "practices", "count"),
    (
        "sections",
        "practice_business_state",
        "practices",
        "authorization_billing_state_hmac_sha256",
    ),
    (
        "sections",
        "practice_business_state",
        "practices",
        "billing_exempt_count",
    ),
    (
        "sections",
        "practice_business_state",
        "practices",
        "clickwrap_baa_count",
    ),
    (
        "sections",
        "practice_business_state",
        "practices",
        "negotiated_baa_on_file_count",
    ),
    (
        "sections",
        "practice_business_state",
        "practices",
        "negotiated_baa_required_count",
    ),
    (
        "sections",
        "practice_business_state",
        "practices",
        "organization_linked_count",
    ),
    (
        "sections",
        "practice_business_state",
        "practices",
        "stripe_customer_count",
    ),
    (
        "sections",
        "practice_business_state",
        "practices",
        "stripe_subscription_count",
    ),
    ("sections", "practice_business_state", "practices", "trial_count"),
    ("sections", "practice_business_state", "memberships", "count"),
    (
        "sections",
        "practice_business_state",
        "memberships",
        "authorization_state_hmac_sha256",
    ),
    (
        "sections",
        "practice_business_state",
        "memberships",
        "active_count",
    ),
    (
        "sections",
        "practice_business_state",
        "memberships",
        "attested_count",
    ),
    (
        "sections",
        "practice_business_state",
        "memberships",
        "organization_authorized_count",
    ),
    (
        "sections",
        "practice_business_state",
        "memberships",
        "revoked_count",
    ),
    ("sections", "organization_access_state", "organizations", "count"),
    (
        "sections",
        "organization_access_state",
        "organizations",
        "state_hmac_sha256",
    ),
    (
        "sections",
        "organization_access_state",
        "organizations",
        "active_count",
    ),
    (
        "sections",
        "organization_access_state",
        "organizations",
        "suspended_count",
    ),
    ("sections", "organization_access_state", "roster", "count"),
    (
        "sections",
        "organization_access_state",
        "roster",
        "state_hmac_sha256",
    ),
    ("sections", "organization_access_state", "roster", "active_count"),
    ("sections", "organization_access_state", "roster", "claimed_count"),
    ("sections", "organization_access_state", "roster", "revoked_count"),
    ("sections", "identity_recovery", "rebind_audit"),
    ("sections", "identity_recovery", "authority_external_identities"),
    ("sections", "identity_recovery", "authority_person_bindings"),
    ("sections", "identity_recovery", "authority_tenant_memberships"),
    ("sections", "provider_credentials", "video_integrations", "count"),
    (
        "sections",
        "provider_credentials",
        "video_integrations",
        "stable_binding_state_hmac_sha256",
    ),
    ("sections", "continuity", "base_grants", "count"),
    ("sections", "continuity", "base_grants", "state_hmac_sha256"),
    ("sections", "continuity", "base_grants", "active_count"),
    ("sections", "continuity", "base_grants", "doctor_snapshot_count"),
    ("sections", "continuity", "base_grants", "prelogin_snapshot_count"),
    ("sections", "continuity", "base_grants", "revoked_count"),
)

EXPECTED_P0_PATHS: Mapping[str, tuple[str, ...]] = {
    "continuity_amendment_rows": (
        "sections",
        "continuity",
        "amendments",
        "count",
    ),
    "continuity_declared_accounts": (
        "sections",
        "continuity",
        "amendments",
        "declared_account_count",
    ),
    "continuity_identity_rows": (
        "sections",
        "continuity",
        "amendment_identities",
        "count",
    ),
    "continuity_safe_prelogin_identities": (
        "sections",
        "continuity",
        "amendment_identities",
        "safe_prelogin_count",
    ),
    "continuity_amendment_grant_rows": (
        "sections",
        "continuity",
        "amendment_grants",
        "count",
    ),
    "continuity_amendment_grant_active_count": (
        "sections",
        "continuity",
        "amendment_grants",
        "active_count",
    ),
    "continuity_amendment_grant_doctor_snapshot_count": (
        "sections",
        "continuity",
        "amendment_grants",
        "doctor_snapshot_count",
    ),
    "continuity_amendment_grant_prelogin_count": (
        "sections",
        "continuity",
        "amendment_grants",
        "prelogin_snapshot_count",
    ),
    "continuity_amendment_grant_revoked_count": (
        "sections",
        "continuity",
        "amendment_grants",
        "revoked_count",
    ),
    "superadmin_grant_rows": (
        "sections",
        "superadmin_capabilities",
        "grants",
        "count",
    ),
    "superadmin_active_grants": (
        "sections",
        "superadmin_capabilities",
        "grants",
        "active_count",
    ),
    "superadmin_platform_admin_active_grants": (
        "sections",
        "superadmin_capabilities",
        "grants",
        "platform_admin_active_count",
    ),
    "superadmin_view_as_active_grants": (
        "sections",
        "superadmin_capabilities",
        "grants",
        "view_as_active_count",
    ),
    "superadmin_revoked_grants": (
        "sections",
        "superadmin_capabilities",
        "grants",
        "revoked_count",
    ),
    "superadmin_audit_rows": (
        "sections",
        "superadmin_capabilities",
        "audit",
        "count",
    ),
    "superadmin_granted_audits": (
        "sections",
        "superadmin_capabilities",
        "audit",
        "granted_count",
    ),
    "superadmin_revoked_audits": (
        "sections",
        "superadmin_capabilities",
        "audit",
        "revoked_count",
    ),
    "view_as_session_rows": ("sections", "view_as", "sessions", "count"),
    "view_as_ended_sessions": (
        "sections",
        "view_as",
        "sessions",
        "ended_count",
    ),
    "view_as_unended_sessions": (
        "sections",
        "view_as",
        "sessions",
        "unended_count",
    ),
    "view_as_audit_rows": ("sections", "view_as", "audit", "count"),
    "view_as_accepted_audits": (
        "sections",
        "view_as",
        "audit",
        "accepted_count",
    ),
    "view_as_began_audits": (
        "sections",
        "view_as",
        "audit",
        "began_count",
    ),
    "view_as_denied_audits": (
        "sections",
        "view_as",
        "audit",
        "denied_count",
    ),
    "view_as_ended_audits": (
        "sections",
        "view_as",
        "audit",
        "ended_count",
    ),
    "view_as_expired_audits": (
        "sections",
        "view_as",
        "audit",
        "expired_count",
    ),
    "view_as_rejected_audits": (
        "sections",
        "view_as",
        "audit",
        "rejected_count",
    ),
    "view_as_stale_cleared_audits": (
        "sections",
        "view_as",
        "audit",
        "stale_cleared_count",
    ),
}

EXPECTED_PLAN_PATHS: Mapping[str, tuple[tuple[str, ...], str]] = {
    "continuity_plan_sha256s": (
        (
            "sections",
            "continuity",
            "amendments",
            "plan_multiset_hmac_sha256",
        ),
        "continuity-amendment-plans",
    ),
    "continuity_grant_plan_sha256s": (
        (
            "sections",
            "continuity",
            "amendment_grants",
            "plan_multiset_hmac_sha256",
        ),
        "continuity-amendment-grant-plans",
    ),
    "superadmin_grant_plan_sha256s": (
        (
            "sections",
            "superadmin_capabilities",
            "grants",
            "grant_plan_multiset_hmac_sha256",
        ),
        "superadmin-grant-plans",
    ),
    "superadmin_audit_evidence_sha256s": (
        (
            "sections",
            "superadmin_capabilities",
            "audit",
            "evidence_multiset_hmac_sha256",
        ),
        "superadmin-audit-evidence",
    ),
}


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ComparisonFailure("protected JSON file contains duplicate keys")
        result[key] = value
    return result


def _load_owner_only_json(path: Path) -> dict[str, Any]:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or not 0 < before.st_size <= MAXIMUM_JSON_BYTES
        ):
            raise ComparisonFailure("protected JSON file is unsafe")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise ComparisonFailure("protected JSON file changed while opening")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                contents = handle.read(MAXIMUM_JSON_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(contents) > MAXIMUM_JSON_BYTES:
            raise ComparisonFailure("protected JSON file is too large")
        value = json.loads(contents, object_pairs_hook=_reject_duplicate_json_keys)
    except ComparisonFailure:
        raise
    except Exception:
        raise ComparisonFailure("protected JSON file could not be read") from None
    if not isinstance(value, dict):
        raise ComparisonFailure("protected JSON file has an invalid shape")
    return value


def _get_path(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            raise ComparisonFailure("evidence snapshot is incomplete")
        current = current[key]
    return current


def _verify_snapshot(
    snapshot: Mapping[str, Any],
    key: bytes,
    *,
    phase: str,
) -> None:
    if (
        snapshot.get("capture_phase") != phase
        or snapshot.get("environment") != evidence.SNAPSHOT_ENVIRONMENT
        or snapshot.get("schema_version") != evidence.SNAPSHOT_SCHEMA_VERSION
        or snapshot.get("digest_scheme") != "hmac-sha256/canonical-json/v1"
    ):
        raise ComparisonFailure("evidence snapshot metadata is invalid")

    expected_key_fingerprint = evidence._hmac_sha256(
        key,
        purpose="key-fingerprint",
        value=b"fixed-public-verifier",
    )
    if not hmac.compare_digest(
        str(snapshot.get("key_fingerprint_hmac_sha256", "")),
        expected_key_fingerprint,
    ):
        raise ComparisonFailure("evidence snapshot key is invalid")

    unsigned = dict(snapshot)
    supplied_snapshot_hmac = unsigned.pop("snapshot_hmac_sha256", None)
    expected_snapshot_hmac = evidence._hmac_sha256(
        key,
        purpose="snapshot",
        value=evidence._canonical_json(unsigned),
    )
    if not isinstance(supplied_snapshot_hmac, str) or not hmac.compare_digest(
        supplied_snapshot_hmac,
        expected_snapshot_hmac,
    ):
        raise ComparisonFailure("evidence snapshot authentication failed")

    schema = snapshot.get("p0_schema")
    expected_present = 0 if phase == "pre" else len(evidence.P0_SCHEMA_TABLES)
    if (
        not isinstance(schema, Mapping)
        or schema.get("expected_table_count") != len(evidence.P0_SCHEMA_TABLES)
        or schema.get("present_count") != expected_present
        or schema.get("presence")
        != {table: phase == "post" for table in sorted(evidence.P0_SCHEMA_TABLES)}
    ):
        raise ComparisonFailure("evidence snapshot schema transition is invalid")


def compare_snapshots(
    pre: Mapping[str, Any],
    post: Mapping[str, Any],
    expected_p0: Mapping[str, Any],
    key: bytes,
) -> None:
    """Raise unless protected state is unchanged and P0 counts are exact."""

    _verify_snapshot(pre, key, phase="pre")
    _verify_snapshot(post, key, phase="post")

    for path in UNCHANGED_PATHS:
        if _get_path(pre, path) != _get_path(post, path):
            raise ComparisonFailure("protected business state changed")

    if set(expected_p0) != set(EXPECTED_P0_PATHS) | set(EXPECTED_PLAN_PATHS):
        raise ComparisonFailure("P0 expectations have an invalid shape")
    if any(
        type(expected_p0[name]) is not int or expected_p0[name] < 0
        for name in EXPECTED_P0_PATHS
    ):
        raise ComparisonFailure("P0 expectations have an invalid shape")
    for name, path in EXPECTED_P0_PATHS.items():
        if _get_path(post, path) != expected_p0[name]:
            raise ComparisonFailure("P0 additions differ from the reviewed plan")

    for name, (path, purpose) in EXPECTED_PLAN_PATHS.items():
        plan_sha256s = expected_p0[name]
        if not isinstance(plan_sha256s, list) or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in plan_sha256s
        ):
            raise ComparisonFailure("P0 expectations have an invalid shape")
        expected_digest = evidence._hmac_sha256(
            key,
            purpose=purpose,
            value=evidence._canonical_json(sorted(plan_sha256s)),
        )
        if not hmac.compare_digest(str(_get_path(post, path)), expected_digest):
            raise ComparisonFailure("P0 additions differ from the reviewed plan")

    for path in (
        ("sections", "continuity", "amendments", "non_staging_count"),
        ("sections", "superadmin_capabilities", "grants", "non_staging_count"),
        ("sections", "superadmin_capabilities", "audit", "non_staging_count"),
        ("sections", "view_as", "sessions", "non_staging_count"),
        ("sections", "view_as", "audit", "non_staging_count"),
    ):
        if _get_path(post, path) != 0:
            raise ComparisonFailure("P0 additions contain another environment")

    if (
        len(expected_p0["continuity_plan_sha256s"])
        != expected_p0["continuity_amendment_rows"]
        or len(expected_p0["continuity_grant_plan_sha256s"])
        != expected_p0["continuity_amendment_grant_rows"]
        or len(expected_p0["superadmin_grant_plan_sha256s"])
        != expected_p0["superadmin_grant_rows"]
        or len(expected_p0["superadmin_audit_evidence_sha256s"])
        != expected_p0["superadmin_audit_rows"]
        or expected_p0["continuity_declared_accounts"]
        != expected_p0["continuity_identity_rows"]
        or expected_p0["continuity_amendment_grant_rows"]
        != expected_p0["continuity_identity_rows"]
        or expected_p0["continuity_amendment_grant_active_count"]
        != expected_p0["continuity_amendment_grant_rows"]
        or expected_p0["continuity_amendment_grant_doctor_snapshot_count"]
        != expected_p0["continuity_amendment_grant_rows"]
        or expected_p0["continuity_amendment_grant_prelogin_count"] != 0
        or expected_p0["continuity_amendment_grant_revoked_count"] != 0
        or expected_p0["continuity_safe_prelogin_identities"] != 0
        or expected_p0["superadmin_active_grants"]
        != expected_p0["superadmin_platform_admin_active_grants"]
        + expected_p0["superadmin_view_as_active_grants"]
        or expected_p0["superadmin_active_grants"]
        + expected_p0["superadmin_revoked_grants"]
        != expected_p0["superadmin_grant_rows"]
        or expected_p0["superadmin_revoked_grants"] != 0
        or expected_p0["superadmin_granted_audits"]
        + expected_p0["superadmin_revoked_audits"]
        != expected_p0["superadmin_audit_rows"]
        or expected_p0["superadmin_revoked_audits"] != 0
        or expected_p0["view_as_ended_sessions"]
        + expected_p0["view_as_unended_sessions"]
        != expected_p0["view_as_session_rows"]
        or expected_p0["view_as_unended_sessions"] != 0
        or expected_p0["view_as_session_rows"] != 0
        or expected_p0["view_as_began_audits"]
        + expected_p0["view_as_ended_audits"]
        + expected_p0["view_as_expired_audits"]
        + expected_p0["view_as_rejected_audits"]
        + expected_p0["view_as_stale_cleared_audits"]
        != expected_p0["view_as_audit_rows"]
        or expected_p0["view_as_accepted_audits"] + expected_p0["view_as_denied_audits"]
        != expected_p0["view_as_audit_rows"]
        or expected_p0["view_as_audit_rows"] != 0
    ):
        raise ComparisonFailure("P0 expectations violate rollout invariants")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare authenticated P0 staging evidence without emitting it.",
    )
    parser.add_argument("--pre", required=True, type=Path)
    parser.add_argument("--post", required=True, type=Path)
    parser.add_argument("--expectations", required=True, type=Path)
    parser.add_argument("--hmac-key-file", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        key = evidence.load_private_hmac_key(args.hmac_key_file)
        pre = _load_owner_only_json(args.pre)
        post = _load_owner_only_json(args.post)
        expected_p0 = _load_owner_only_json(args.expectations)
        compare_snapshots(pre, post, expected_p0, key)
    except Exception:
        print("P0 staging evidence comparison failed.", file=sys.stderr)
        return 2
    print("P0 staging evidence comparison passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
