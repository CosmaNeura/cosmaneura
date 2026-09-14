"""Bounded staging snapshot core, with no live adapter or executable consumer.

The separate human-only launcher must establish reviewed source/runtime/plan,
explicit current identity, two foreground confirmations, exact independent
plan/handoff review and start-time expiry BEFORE calling this core. This module
does not implement, mint, renew, or substitute for any of those authority gates.
Tests supply in-memory operations only. There is deliberately no live CLI here.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

SERVER = "cosmaneura-staging-postgres"
DATABASE = "staging"
HOST = SERVER + ".postgres.database.azure.com"
SOURCE_REVISION = "89c31f0ae275"
MAX_ACCESS_SECONDS = 1800
MAX_CAPTURE_SECONDS = 1200
CLEANUP_RESERVE_SECONDS = 300
MAX_ARCHIVE_BYTES = 1024**3


class CaptureRefused(RuntimeError):
    """Only fixed non-sensitive codes may be surfaced by the human launcher."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise CaptureRefused(code)


def valid_sha(value: str) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class Rule:
    name: str
    start: str
    end: str


@dataclass(frozen=True)
class CaptureInputs:
    # These values are inputs to bounded execution, NOT proof of authorization.
    rule: Rule
    source_commit: str
    plan_sha256: str
    identity_evidence_sha256: str
    workspace_manifest_sha256: str
    plan_created_at: datetime
    plan_expires_at: datetime
    handoff_created_at: datetime
    handoff_expires_at: datetime

    def validate(self, now: datetime) -> None:
        require(type(self.rule) is Rule, "rule_type")
        require(
            re.fullmatch(r"cosmaneura-snapshot-[0-9a-f]{16}", self.rule.name),
            "rule_name",
        )
        address = ipaddress.ip_address(self.rule.start)
        require(
            type(address) is ipaddress.IPv4Address and address.is_global,
            "public_ipv4_required",
        )
        require(str(address) == self.rule.start == self.rule.end, "single_exact_ipv4")
        require(
            type(self.source_commit) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_commit),
            "source_commit",
        )
        require(
            all(
                valid_sha(value)
                for value in (
                    self.plan_sha256,
                    self.identity_evidence_sha256,
                    self.workspace_manifest_sha256,
                )
            ),
            "evidence_hash",
        )
        dates = (
            now,
            self.plan_created_at,
            self.plan_expires_at,
            self.handoff_created_at,
            self.handoff_expires_at,
        )
        require(
            all(type(value) is datetime and value.tzinfo is UTC for value in dates),
            "utc_required",
        )
        require(
            self.plan_expires_at == self.plan_created_at + timedelta(hours=4),
            "plan_lifetime",
        )
        require(
            self.handoff_expires_at == self.handoff_created_at + timedelta(minutes=10),
            "handoff_lifetime",
        )
        require(
            self.plan_created_at <= self.handoff_created_at <= now,
            "future_or_reordered_artifact",
        )
        require(
            now + timedelta(seconds=MAX_ACCESS_SECONDS) < self.plan_expires_at,
            "plan_remaining_lifetime",
        )
        require(
            now + timedelta(seconds=120) < self.handoff_expires_at,
            "handoff_remaining_lifetime",
        )


@dataclass(frozen=True)
class Archive:
    size: int
    sha256: str
    source_revision: str
    # Adapter confirms pg_dump custom format, complete process success and
    # descriptor-bound encrypted output, never just a file's existence.
    custom_format_complete: bool
    encrypted_descriptor_bound: bool

    def validate(self) -> None:
        require(
            type(self.size) is int and 0 < self.size <= MAX_ARCHIVE_BYTES,
            "archive_size",
        )
        require(valid_sha(self.sha256), "archive_digest")
        require(self.source_revision == SOURCE_REVISION, "source_revision_changed")
        require(
            self.custom_format_complete is True
            and self.encrypted_descriptor_bound is True,
            "archive_not_complete_or_bound",
        )


class Operations(Protocol):
    """No implementation is supplied in this PR. Every timeout is mandatory.

    A later reviewed live adapter must bound child processes and HTTP operations,
    use the exact staging server/subscription, retain authenticated response
    dates and the exact ARM-token identity, refuse redirects/ambient overrides,
    and expose only value-minimized results. Test doubles confer no authority.
    """

    def monotonic(self) -> float: ...
    def utcnow(self) -> datetime: ...
    def revalidate_human_gate(self, inputs: CaptureInputs, timeout: float) -> None: ...
    def revalidate_runtime_identity(
        self, inputs: CaptureInputs, timeout: float
    ) -> None: ...
    def current_public_ipv4(self, timeout: float) -> str: ...
    def list_rules(self, timeout: float) -> tuple[Rule, ...]: ...
    def verify_empty_encrypted_payload(
        self, inputs: CaptureInputs, timeout: float
    ) -> None: ...
    def create_exact_rule(self, rule: Rule, timeout: float) -> None: ...
    def capture_to_exclusive_encrypted_descriptor(
        self, inputs: CaptureInputs, timeout: float, max_bytes: int
    ) -> Archive: ...
    def cancel_capture_and_reap(self, timeout: float) -> None: ...
    def delete_exact_rule(self, rule: Rule, timeout: float) -> None: ...
    def publish_metadata(self, metadata: dict[str, object], timeout: float) -> None: ...


class Clock:
    def __init__(self, operations: Operations) -> None:
        self.operations = operations
        self.started = operations.monotonic()
        self.started_utc = operations.utcnow()
        require(
            type(self.started) in (int, float) and math.isfinite(self.started), "clock"
        )
        require(
            type(self.started_utc) is datetime and self.started_utc.tzinfo is UTC,
            "clock",
        )
        self.last = self.started

    def elapsed(self) -> float:
        current = self.operations.monotonic()
        wall = self.operations.utcnow()
        require(
            type(current) in (int, float)
            and math.isfinite(current)
            and current >= self.last,
            "clock_regression",
        )
        require(type(wall) is datetime and wall.tzinfo is UTC, "clock")
        self.last = current
        elapsed = current - self.started
        # Neither a wall-clock jump nor a frozen monotonic clock extends access.
        require(
            abs((wall - self.started_utc).total_seconds() - elapsed) <= 5,
            "clock_divergence",
        )
        return elapsed

    def remaining(self, deadline: float, cap: float) -> float:
        remaining = min(cap, deadline - self.elapsed())
        require(remaining > 0, "action_deadline")
        return remaining


def rules_by_name(rules: tuple[Rule, ...]) -> dict[str, Rule]:
    require(
        type(rules) is tuple and all(type(rule) is Rule for rule in rules),
        "rule_inventory_shape",
    )
    result = {rule.name: rule for rule in rules}
    require(len(result) == len(rules), "duplicate_rule_names")
    return result


def without_new_rule(rules: dict[str, Rule], rule: Rule) -> dict[str, Rule]:
    return {name: value for name, value in rules.items() if name != rule.name}


def close_exact_access(
    operations: Operations, rule: Rule, baseline: dict[str, Rule]
) -> None:
    """Safety cleanup is not blocked by expired receipts or a failed clock.

    No generalized rule deletion and no existing-rule repair. If creation had
    an unknown outcome, inspect before deleting. Matching the exact rule is
    mandatory. Cleanup failure must be reported; never claim automatic Azure TTL.
    """
    cancellation_failed = False
    try:
        operations.cancel_capture_and_reap(timeout=15)
    except BaseException:
        # A stuck child must not prevent best-effort closure of its access rule.
        cancellation_failed = True
    rules = rules_by_name(operations.list_rules(timeout=30))
    if rule.name in rules:
        require(rules[rule.name] == rule, "cleanup_rule_changed_human_action_required")
        try:
            operations.delete_exact_rule(rule, timeout=60)
        except BaseException:
            # Unknown DELETE outcome: one readback, no replay or blind retry.
            pass
    after = rules_by_name(operations.list_rules(timeout=30))
    require(rule.name not in after, "cleanup_not_confirmed_human_action_required")
    require(after == baseline, "preexisting_rules_changed_human_action_required")
    require(not cancellation_failed, "capture_stop_failed_human_action_required")


def execute_after_human_gate(
    inputs: CaptureInputs, operations: Operations
) -> dict[str, object]:
    """Run only after the separate reviewed human launcher establishes authority.

    No retries, new approval, claim, receipt renewal, shell, snapshot overwrite,
    role/grant creation, live migration, deployment, or restore is implemented.
    All I/O comes from the future exact live adapter. This core is not usable as
    an independent authorization boundary and must not be presented as one.
    """
    require(type(inputs) is CaptureInputs, "input_type")
    clock = Clock(operations)
    inputs.validate(operations.utcnow())
    # Preparation consumes the same bounded execution clock; no timer reset.
    operations.revalidate_human_gate(inputs, clock.remaining(120, 30))
    operations.revalidate_runtime_identity(inputs, clock.remaining(120, 30))
    require(
        operations.current_public_ipv4(clock.remaining(120, 15)) == inputs.rule.start,
        "public_ip_changed",
    )
    baseline = rules_by_name(operations.list_rules(clock.remaining(120, 30)))
    require(inputs.rule.name not in baseline, "planned_rule_already_exists")
    operations.verify_empty_encrypted_payload(inputs, clock.remaining(120, 30))
    operations.revalidate_human_gate(inputs, clock.remaining(120, 30))
    inputs.validate(operations.utcnow())
    # Intent is in memory only; no one-use claim is created. Set before PUT so
    # timeout/unknown result still reaches exact inspected cleanup.
    create_attempted = False
    archive: Archive | None = None
    failed = False
    try:
        create_attempted = True
        operations.create_exact_rule(inputs.rule, clock.remaining(150, 30))
        observed = rules_by_name(operations.list_rules(clock.remaining(180, 30)))
        require(observed.get(inputs.rule.name) == inputs.rule, "new_rule_not_exact")
        require(
            without_new_rule(observed, inputs.rule) == baseline,
            "preexisting_rules_changed",
        )
        require(
            operations.current_public_ipv4(clock.remaining(180, 15))
            == inputs.rule.start,
            "public_ip_changed",
        )
        operations.revalidate_runtime_identity(inputs, clock.remaining(210, 30))
        operations.verify_empty_encrypted_payload(inputs, clock.remaining(210, 30))
        # A live adapter opens the verified encrypted payload with no-follow and
        # exclusive creation. Timeout/max bytes must be enforced while streaming,
        # not merely checked after pg_dump returns.
        archive = operations.capture_to_exclusive_encrypted_descriptor(
            inputs,
            clock.remaining(
                MAX_ACCESS_SECONDS - CLEANUP_RESERVE_SECONDS, MAX_CAPTURE_SECONDS
            ),
            MAX_ARCHIVE_BYTES,
        )
        require(type(archive) is Archive, "archive_type")
        archive.validate()
        operations.revalidate_runtime_identity(inputs, clock.remaining(1500, 30))
        clock.remaining(1500, 1)
    except BaseException:
        failed = True
    finally:
        if create_attempted:
            try:
                close_exact_access(operations, inputs.rule, baseline)
            except BaseException:
                # Generic code never exposes provider diagnostics or secrets.
                raise CaptureRefused("cleanup_failed_human_action_required") from None
    elapsed = clock.elapsed()
    require(
        elapsed < MAX_ACCESS_SECONDS, "access_window_exceeded_human_action_required"
    )
    if failed or archive is None:
        raise CaptureRefused("capture_failed_access_closed")
    archive.validate()
    metadata: dict[str, object] = {
        "schema_version": 1,
        "status": "captured_access_closed",
        "source_commit": inputs.source_commit,
        "plan_sha256": inputs.plan_sha256,
        "identity_evidence_sha256": inputs.identity_evidence_sha256,
        "workspace_manifest_sha256": inputs.workspace_manifest_sha256,
        "archive_sha256": archive.sha256,
        "archive_bytes": archive.size,
        "source_revision": archive.source_revision,
        "rule_name_sha256": hashlib.sha256(inputs.rule.name.encode()).hexdigest(),
        "new_rule_absent": True,
        "preexisting_rules_unchanged": True,
        "elapsed_seconds": round(elapsed, 3),
        "restore_or_migration_accepted": False,
        "deployment_authorized": False,
        "not_a_receipt_or_reusable_authorization": True,
    }
    # Success publication is after confirmed closure, never a substitute for it.
    operations.publish_metadata(metadata, timeout=15)
    return metadata


if __name__ == "__main__":
    raise SystemExit("snapshot_core_has_no_live_adapter_or_human_entrypoint")
