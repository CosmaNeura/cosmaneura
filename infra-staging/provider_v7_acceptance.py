"""Operator acceptance for the immutable candidate and closed staging failures."""

from __future__ import annotations

import hashlib
import runpy
import sys

KNOWN_FAILED = frozenset(
    {
        "94527b2939fa327ee1a355c94749ef507e47db88445f3f8cabefc2befb44a340",
        "07267ba4a37d268ec8fb1c0a60daef0d3bb14f64262c8236ba73e4f97394d83e",
    }
)


def assert_status() -> None:
    from app.services.authority_reconciliation import incomplete_ingestion_status

    report = incomplete_ingestion_status(older_than_minutes=15, limit=1000)
    count = len(KNOWN_FAILED)
    if (
        report.matching_interaction_count != count
        or len(report.interactions) != count
        or report.pending_audit_count != 0
        or report.pending_usage_count != 0
        or report.failed_audit_count != count
        or report.failed_usage_count != count
        or {
            (
                hashlib.sha256(row.interaction_ref.encode()).hexdigest(),
                row.audit_status,
                row.usage_status,
            )
            for row in report.interactions
        }
        != {(digest, "failed", "failed") for digest in KNOWN_FAILED}
    ):
        raise RuntimeError("Staging ingestion baseline mismatch")


def main() -> int:
    status_only = sys.argv[1:] in (["status"], ["--status-only"])
    marker = "V7_STATUS" if status_only else "V7_PROVIDER_SMOKE"
    try:
        if not status_only and len(sys.argv) != 4:
            raise ValueError
        candidate = runpy.run_path("/app/infra-staging/provider_v7_smoke.py")
        candidate["_require_staging"]()
        assert_status()
        if not status_only:
            candidate["run"](*sys.argv[1:])
            assert_status()
    except Exception:
        print(f"{marker} FAIL")
        return 1
    print(f"{marker} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
