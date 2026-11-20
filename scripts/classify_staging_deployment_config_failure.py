#!/usr/bin/env python3
"""Classify staging deploy config-load stderr into a closed reason set.

Reads captured resolver stderr and emits exactly one value-free line:
  reason=<code>
  reason=<code> key=<KEY>
  reason=guard_failed guard=<name>

Never prints stderr text, secret values, tokens, or op:// references.
Stdlib only; intended for ``python3 -I -S -B``.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REASON_CODES = frozenset(
    {
        "op_rate_limited",
        "op_auth_failed",
        "op_network",
        "op_ref_not_found",
        "op_field_not_found",
        "op_other",
        "guard_failed",
        "unclassified",
    }
)

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,80}$")
KEY_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,80})\b")
CONFIG_KEY_MARKER_RE = re.compile(
    r"(?m)^(?:config_key|CONFIG_KEY)=([A-Z][A-Z0-9_]{1,80})\s*$"
)
GUARD_MARKER_RE = re.compile(r"(?m)^(?:guard|GUARD)=([a-z][a-z0-9_]{1,80})\s*$")
EXPORT_KEY_RE = re.compile(r"(?m)^(?:export\s+)?([A-Z][A-Z0-9_]{1,80})=")

# Prefer specific 1Password failure modes before the generic op_other bucket.
OP_RATE_LIMITED = re.compile(
    r"(?i)\b(429|too many requests|rate[\s_-]?limit(?:ed|ing)?)\b"
)
OP_AUTH_FAILED = re.compile(
    r"(?i)\b("
    r"not\s+currently\s+signed\s+in|"
    r"authentication\s+required|"
    r"unauthorized|"
    r"forbidden|"
    r"invalid(?:\s+or\s+expired)?\s+(?:token|credentials?)|"
    r"expired\s+(?:token|credentials?)|"
    r"service\s+account\s+token|"
    r"OP_SERVICE_ACCOUNT_TOKEN|"
    r"account\s+is\s+not\s+signed\s+in|"
    r"you\s+are\s+not\s+currently\s+signed\s+in"
    r")\b"
)
OP_NETWORK = re.compile(
    r"(?i)\b("
    r"timed?\s*out|timeout|"
    r"connection\s+refused|"
    r"connection\s+reset|"
    r"network\s+is\s+unreachable|"
    r"temporary\s+failure\s+in\s+name\s+resolution|"
    r"name\s+or\s+service\s+not\s+known|"
    r"could\s+not\s+resolve|"
    r"dns|"
    r"tls|ssl|"
    r"certificate\s+verify\s+failed|"
    r"handshake\s+failure|"
    r"\b5\d{2}\b|"
    r"server\s+error|"
    r"bad\s+gateway|"
    r"service\s+unavailable|"
    r"gateway\s+timeout"
    r")\b"
)
OP_FIELD_NOT_FOUND = re.compile(
    r"(?i)\b("
    r"field\s+not\s+found|"
    r"unknown\s+field|"
    r"no\s+field\s+named|"
    r"missing\s+field"
    r")\b"
)
OP_REF_NOT_FOUND = re.compile(
    r"(?i)\b("
    r"item\s+not\s+found|"
    r"not\s+found\s+in\s+(?:vault|account)|"
    r"could\s+not\s+find\s+(?:item|secret|reference)|"
    r"no\s+item\s+(?:found|matches)|"
    r"secret\s+not\s+found|"
    r"reference\s+not\s+found|"
    r"op://[^\s'\"]+\s+(?:was\s+)?not\s+found|"
    r"\[ERROR\].*not\s+found"
    r")\b"
)
OP_GENERIC = re.compile(
    r"(?i)\b("
    r"\bop\b|"
    r"1password|"
    r"op\s+inject|"
    r"op\s+read|"
    r"op://"
    r")"
)
GUARD_FAILED_TEXT = re.compile(
    r"(?i)\b("
    r"core\s+cab|"
    r"invalid_core_cab|"
    r"Core CAB|"
    r"staging_deployment_database_tls|"
    r"staging_recall_key_preservation|"
    r"recall_api_key_live_guard|"
    r"STRIPE_BILLING_ITEM_ID\s+is\s+required|"
    r"guard_failed|"
    r"is\s+unavailable|"
    r"Invalid Core CAB"
    r")"
)


def _allowed_keys(env_file: Path) -> frozenset[str]:
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    return frozenset(EXPORT_KEY_RE.findall(text))


def _safe_key(candidate: str | None, allowed: frozenset[str]) -> str | None:
    if candidate is None:
        return None
    if KEY_RE.fullmatch(candidate) is None:
        return None
    if candidate not in allowed:
        return None
    return candidate


def _extract_key(stderr: str, allowed: frozenset[str]) -> str | None:
    marker = CONFIG_KEY_MARKER_RE.search(stderr)
    if marker is not None:
        return _safe_key(marker.group(1), allowed)
    # Prefer export-shaped mentions that are allowlisted.
    for match in EXPORT_KEY_RE.finditer(stderr):
        key = _safe_key(match.group(1), allowed)
        if key is not None:
            return key
    # Last resort: first allowlisted KEY token that is not a marker label.
    for match in KEY_TOKEN_RE.finditer(stderr):
        token = match.group(1)
        if token in {"CONFIG_KEY", "GUARD"}:
            continue
        key = _safe_key(token, allowed)
        if key is not None:
            return key
    return None


def _extract_guard(stderr: str) -> str | None:
    marker = GUARD_MARKER_RE.search(stderr)
    if marker is not None:
        return marker.group(1)
    lowered = stderr.lower()
    if "core cab" in lowered or "core_cab" in lowered or "invalid_core_cab" in lowered:
        return "core_cab_runtime_config"
    if "staging_deployment_database_tls" in lowered or (
        "database" in lowered and "tls" in lowered
    ):
        return "staging_deployment_database_tls"
    if "recall" in lowered and ("guard" in lowered or "preservation" in lowered):
        return "staging_recall_api_key_live_guard"
    if "stripe_billing_item_id" in lowered:
        return "stripe_billing_item"
    return None


def classify(stderr: str, allowed_keys: frozenset[str]) -> str:
    """Map stderr to a closed reason line. Never echo stderr."""
    if not isinstance(stderr, str):
        return "reason=unclassified"

    key = _extract_key(stderr, allowed_keys)
    guard_marker = GUARD_MARKER_RE.search(stderr)
    guard = _extract_guard(stderr)

    # Explicit failure markers win over heuristic op:// / CLI text matching so
    # a guard that happens to mention an op reference still classifies as a
    # guard failure without leaking the reference text.
    if guard_marker is not None:
        reason = "guard_failed"
    elif OP_RATE_LIMITED.search(stderr):
        reason = "op_rate_limited"
    elif OP_AUTH_FAILED.search(stderr):
        reason = "op_auth_failed"
    elif OP_NETWORK.search(stderr):
        reason = "op_network"
    elif OP_FIELD_NOT_FOUND.search(stderr):
        reason = "op_field_not_found"
    elif OP_REF_NOT_FOUND.search(stderr):
        reason = "op_ref_not_found"
    elif OP_GENERIC.search(stderr):
        reason = "op_other"
    elif guard is not None or GUARD_FAILED_TEXT.search(stderr):
        reason = "guard_failed"
    else:
        reason = "unclassified"

    if reason not in REASON_CODES:
        reason = "unclassified"

    parts = [f"reason={reason}"]
    if reason == "guard_failed":
        if guard is not None and re.fullmatch(r"[a-z][a-z0-9_]{1,80}", guard):
            parts.append(f"guard={guard}")
    elif key is not None:
        parts.append(f"key={key}")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--stderr-file", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print("reason=unclassified")
        return 0

    env_path = Path(args.env_file)
    stderr_path = Path(args.stderr_file)
    allowed = _allowed_keys(env_path)
    try:
        # Bound read; never inherit or re-emit the bytes.
        raw = stderr_path.read_bytes()[:65536]
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        text = ""

    print(classify(text, allowed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
