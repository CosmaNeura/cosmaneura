#!/usr/bin/env python3
"""Emit aggregate-only production Session Prep provider eligibility evidence.

This command deliberately does not import ``app``: importing the application in
production can run startup migrations.  It uses one repeatable-read, read-only
transaction and returns fixed authorization-state counts only.  No provider,
practice, account, or chart identifier is selected into the process or emitted.
The entire report is refused if any normalization input is outside printable
ASCII: PostgreSQL's space-only btrim and locale casing are not Python's Unicode
strip/lower contract. This guard does not classify a partially covered cohort.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, LiteralString, Protocol

import psycopg

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MILLISECONDS = 30_000
REPORT_SCHEMA_VERSION = 1
EXPECTED_ENVIRONMENT = "production"
EXPECTED_DATABASE_NAME = "production"
EXPECTED_SCHEMA = "public"
EXPECTED_DATABASE_HOST = "cosmaneura-prod-postgres.postgres.database.azure.com"
LINUX_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
BAA_CURRENT_REFERENCE = "CN-BAA-CUST-CW v2.3"
ROOT = Path(__file__).resolve().parents[1]
# Reviewed runtime ASTs, not imports: application initialization can mutate DBs.
# A substantive normalizer change requires a fresh SQL-contract review.
NORMALIZATION_SOURCE_CONTRACTS = (
    (
        "app/models/practice.py",
        "Practice",
        "has_authoritative_stripe_subscription_binding",
        "c7eeba860a3e53baa56211e3e546dcb92d0efd2b94099fa93a356f75c99838b2",
    ),
    (
        "app/services/organization_access.py",
        None,
        "normalize_email",
        "ba944d3b1277fd1f5f1f30bbe3e16aba8e97a3ca54f3f95c368b907e0c07b2e8",
    ),
    (
        "app/services/organization_access.py",
        None,
        "email_matches_exact_domain",
        "5f55b631a26396281d2b1c9a02b346ca1af324340d7d6ef91f2f532e28c4bf5e",
    ),
)
REQUIRED_SESSION_PREP_ANCESTORS = (
    "443f26a5dfe7",
    "c80db51e6a75",
    "de9260834611",
)

DENIAL_REASONS = (
    "provider_unavailable",
    "pilot_grant_revoked",
    "onboarding_incomplete",
    "practice_membership_required",
    "practice_membership_inactive",
    "practice_access_not_authorized",
    "baa_not_in_force",
    "entitlement_not_in_force",
)
CLASSIFICATIONS = (*DENIAL_REASONS, "eligible")

INTEGRITY_CHECKS = (
    "alembic_head_not_singular",
    "dangling_practice_membership",
    "duplicate_active_pilot_grant",
    "duplicate_active_roster_approval",
    "duplicate_doctor_subject",
    "duplicate_practice_membership",
    "unknown_member_seat_state",
    "unknown_organization_status",
    "unknown_practice_acquisition_source",
    "unknown_practice_subscription_status",
    "unsupported_identity_or_billing_normalization",
)

SERVER_IDENTITY_QUERY: LiteralString = """
SELECT current_database(),
       current_schema(),
       current_setting('transaction_read_only')
"""

TLS_STATUS_QUERY: LiteralString = """
SELECT ssl, version
  FROM pg_stat_ssl
 WHERE pid = pg_backend_pid()
"""

MIGRATION_HEAD_QUERY: LiteralString = """
SELECT version_num
  FROM alembic_version
 ORDER BY version_num
"""

INTEGRITY_QUERY: LiteralString = """
SELECT check_name, violations
  FROM (
        SELECT 'alembic_head_not_singular' AS check_name,
               CASE WHEN count(*) = 1 THEN 0 ELSE 1 END::bigint AS violations
          FROM alembic_version
        UNION ALL
        SELECT 'dangling_practice_membership', count(*)::bigint
          FROM practice_member AS member
          LEFT JOIN practice ON practice.id = member.practice_id
         WHERE practice.id IS NULL
        UNION ALL
        SELECT 'duplicate_active_pilot_grant', count(*)::bigint
          FROM (
                SELECT 1
                  FROM pilot_access_grandfather_grant
                 WHERE revoked_at IS NULL
                 GROUP BY clerk_id
                HAVING count(*) > 1
               ) AS duplicate_active_pilot_grants
        UNION ALL
        SELECT 'duplicate_active_roster_approval', count(*)::bigint
          FROM (
                SELECT 1
                  FROM organization_roster_approval
                 WHERE revoked_at IS NULL
                 GROUP BY approved_email COLLATE "C"
                HAVING count(*) > 1
               ) AS duplicate_active_roster_approvals
        UNION ALL
        SELECT 'duplicate_doctor_subject', count(*)::bigint
          FROM (
                SELECT 1
                  FROM doctor
                 GROUP BY clerk_id
                HAVING count(*) > 1
               ) AS duplicate_doctor_subjects
        UNION ALL
        SELECT 'duplicate_practice_membership', count(*)::bigint
          FROM (
                SELECT 1
                  FROM practice_member
                 GROUP BY doctor_id
                HAVING count(*) > 1
               ) AS duplicate_practice_memberships
        UNION ALL
        SELECT 'unknown_member_seat_state', count(*)::bigint
          FROM practice_member
         WHERE seat_state NOT IN ('active', 'inactive')
            OR seat_state IS NULL
        UNION ALL
        SELECT 'unknown_organization_status', count(*)::bigint
          FROM provider_organization
         WHERE status NOT IN ('active', 'suspended')
            OR status IS NULL
        UNION ALL
        SELECT 'unknown_practice_acquisition_source', count(*)::bigint
          FROM practice
         WHERE acquisition_source NOT IN ('managed', 'public_self_service')
            OR acquisition_source IS NULL
        UNION ALL
        SELECT 'unknown_practice_subscription_status', count(*)::bigint
          FROM practice
         WHERE subscription_status IS NOT NULL
           AND subscription_status NOT IN (
                'incomplete',
                'incomplete_expired',
                'trialing',
                'active',
                'past_due',
                'canceled',
                'unpaid',
                'paused',
                'pending_verification',
                'invalid'
           )
        UNION ALL
        SELECT 'unsupported_identity_or_billing_normalization', count(*)::bigint
          FROM (
                SELECT email AS value FROM doctor
                UNION ALL
                SELECT approved_email FROM organization_roster_approval
                UNION ALL
                SELECT email_domain FROM provider_organization
                UNION ALL
                SELECT stripe_subscription_id FROM practice
                UNION ALL
                SELECT stripe_customer_id FROM practice
               ) AS normalization_inputs
         WHERE value COLLATE "C" ~ '[^ -~]'
       ) AS checks
 ORDER BY check_name
"""

ELIGIBILITY_QUERY: LiteralString = """
WITH parameters AS (
    SELECT %s::timestamptz AS observed_at,
           %s::text AS current_baa_reference
),
pilot_state AS (
    SELECT clerk_id,
           bool_or(revoked_at IS NULL) AS has_active,
           bool_or(revoked_at IS NOT NULL) AS has_revoked
      FROM pilot_access_grandfather_grant
     GROUP BY clerk_id
),
roster_history AS (
    SELECT approval.approved_email COLLATE "C" AS approved_email,
           bool_or(approval.revoked_at IS NOT NULL) AS has_revoked,
           count(*) FILTER (
               WHERE approval.revoked_at IS NULL
                 AND (
                     approval.expires_at IS NULL
                     OR approval.expires_at > parameters.observed_at
                 )
           ) AS active_count
      FROM organization_roster_approval AS approval
      CROSS JOIN parameters
     GROUP BY approval.approved_email COLLATE "C"
),
provider_state AS (
    SELECT CASE
        WHEN doctor.deleted_at IS NOT NULL
            THEN 'provider_unavailable'
        WHEN NOT coalesce(pilot_state.has_active, false)
         AND coalesce(pilot_state.has_revoked, false)
            THEN 'pilot_grant_revoked'
        WHEN doctor.onboarding_completed_at IS NULL
            THEN 'onboarding_incomplete'
        WHEN member.id IS NULL OR practice.id IS NULL
            THEN 'practice_membership_required'
        WHEN NOT (
            member.access_revoked_at IS NULL
            AND (
                member.seat_state = 'active'
                OR (
                    member.seat_state = 'inactive'
                    AND member.access_ends_at IS NOT NULL
                    AND member.access_ends_at > parameters.observed_at
                )
            )
        )
            THEN 'practice_membership_inactive'
        WHEN NOT CASE
            WHEN practice.organization_id IS NULL
                THEN member.attested_at IS NOT NULL
            ELSE coalesce(
                member.organization_roster_approval_id IS NOT NULL
                AND member.organization_authorized_at IS NOT NULL
                AND roster_approval.id = member.organization_roster_approval_id
                AND roster_approval.revoked_at IS NULL
                AND (
                    roster_approval.expires_at IS NULL
                    OR roster_approval.expires_at > parameters.observed_at
                )
                AND roster_approval.claimed_doctor_id = member.doctor_id
                AND roster_approval.target_practice_id = member.practice_id
                AND doctor.email IS NOT NULL
                AND char_length(btrim(doctor.email)) <= 320
                AND (
                    char_length(btrim(doctor.email))
                    - char_length(replace(btrim(doctor.email), '@', ''))
                ) = 1
                AND split_part(lower(btrim(doctor.email) COLLATE "C"), '@', 1) <> ''
                AND split_part(lower(btrim(doctor.email) COLLATE "C"), '@', 2) <> ''
                AND position(
                    '.' IN split_part(lower(btrim(doctor.email) COLLATE "C"), '@', 2)
                ) > 0
                AND lower(btrim(doctor.email) COLLATE "C")
                    = roster_approval.approved_email
                AND NOT coalesce(roster_history.has_revoked, false)
                AND roster_history.active_count = 1
                AND organization.id = practice.organization_id
                AND organization.status = 'active'
                AND split_part(lower(btrim(doctor.email) COLLATE "C"), '@', 2)
                    = lower(ltrim(btrim(organization.email_domain), '@') COLLATE "C")
                AND char_length(ltrim(btrim(organization.email_domain), '@'))
                    BETWEEN 1 AND 253
                AND position(
                    '@' IN ltrim(btrim(organization.email_domain), '@')
                ) = 0
                AND position(
                    '.' IN ltrim(btrim(organization.email_domain), '@')
                ) > 0
                AND practice.negotiated_baa_on_file
                AND practice.negotiated_baa_reference IS NOT NULL
                AND practice.negotiated_baa_reference <> ''
                AND practice.negotiated_baa_effective_date IS NOT NULL
                AND practice.negotiated_baa_effective_date
                    <= parameters.observed_at,
                false
            )
        END
            THEN 'practice_access_not_authorized'
        WHEN NOT (
            (
                practice.negotiated_baa_on_file
                AND practice.negotiated_baa_reference IS NOT NULL
                AND practice.negotiated_baa_reference <> ''
                AND practice.negotiated_baa_effective_date IS NOT NULL
                AND practice.negotiated_baa_effective_date
                    <= parameters.observed_at
            )
            OR (
                practice.baa_accepted_at IS NOT NULL
                AND practice.baa_version = parameters.current_baa_reference
            )
        )
            THEN 'baa_not_in_force'
        WHEN NOT CASE
            WHEN practice.subscription_status IN ('pending_verification', 'invalid')
                THEN false
            WHEN practice.billing_exempt
             AND (
                practice.billing_exempt_until IS NULL
                OR practice.billing_exempt_until > parameters.observed_at
             )
                THEN true
            WHEN practice.acquisition_source = 'public_self_service'
             AND practice.subscription_status IN ('trialing', 'active')
             AND NOT coalesce(
                left(practice.stripe_subscription_id COLLATE "C", 4) = 'sub_'
                AND char_length(practice.stripe_subscription_id) > 4
                AND btrim(practice.stripe_subscription_id) COLLATE "C"
                    = practice.stripe_subscription_id
                AND left(practice.stripe_customer_id COLLATE "C", 4) = 'cus_'
                AND char_length(practice.stripe_customer_id) > 4
                AND btrim(practice.stripe_customer_id) COLLATE "C"
                    = practice.stripe_customer_id
                AND practice.stripe_state_version > 0,
                false
             )
                THEN false
            WHEN practice.subscription_status = 'active'
                THEN true
            WHEN practice.subscription_status = 'trialing'
                THEN practice.trial_ends_at IS NOT NULL
                 AND practice.trial_ends_at > parameters.observed_at
            WHEN practice.subscription_status IS NOT NULL
                THEN false
            WHEN practice.acquisition_source = 'public_self_service'
                THEN false
            ELSE practice.trial_ends_at IS NOT NULL
             AND practice.trial_ends_at > parameters.observed_at
        END
            THEN 'entitlement_not_in_force'
        ELSE 'eligible'
    END AS classification
      FROM doctor
      CROSS JOIN parameters
      LEFT JOIN practice_member AS member ON member.doctor_id = doctor.id
      LEFT JOIN practice ON practice.id = member.practice_id
      LEFT JOIN pilot_state ON pilot_state.clerk_id = doctor.clerk_id
      LEFT JOIN organization_roster_approval AS roster_approval
        ON roster_approval.id = member.organization_roster_approval_id
      LEFT JOIN roster_history
        ON roster_history.approved_email = lower(btrim(doctor.email) COLLATE "C")
      LEFT JOIN provider_organization AS organization
        ON organization.id = roster_approval.organization_id
)
SELECT classification, count(*)::bigint
  FROM provider_state
 GROUP BY classification
 ORDER BY classification
"""


class InventoryFailure(RuntimeError):
    """A private failure whose details must never reach command output."""


class QueryResult(Protocol):
    def fetchone(self) -> tuple[Any, ...] | None: ...

    def fetchall(self) -> Sequence[tuple[Any, ...]]: ...


class ReadOnlyConnection(Protocol):
    def execute(
        self,
        query: LiteralString,
        params: tuple[Any, ...] = (),
    ) -> QueryResult: ...


def validate_runtime_normalization_contract(root: Path | None = None) -> None:
    """Refuse changed normalizers without importing app or exposing source data."""

    source_root = ROOT if root is None else root
    try:
        for path, scope, name, expected_sha256 in NORMALIZATION_SOURCE_CONTRACTS:
            tree = ast.parse((source_root / path).read_text(encoding="utf-8"))
            body = tree.body
            if scope is not None:
                classes = [
                    node
                    for node in body
                    if isinstance(node, ast.ClassDef) and node.name == scope
                ]
                if len(classes) != 1:
                    raise InventoryFailure("runtime normalization source is ambiguous")
                body = classes[0].body
            functions = [
                node
                for node in body
                if isinstance(node, ast.FunctionDef) and node.name == name
            ]
            if (
                len(functions) != 1
                or hashlib.sha256(
                    ast.dump(functions[0], include_attributes=False).encode()
                ).hexdigest()
                != expected_sha256
            ):
                raise InventoryFailure("runtime normalization source changed")
    except (OSError, SyntaxError, UnicodeDecodeError) as error:
        raise InventoryFailure("runtime normalization source cannot be read") from error


@dataclass(frozen=True, slots=True)
class RepositoryMigrationContract:
    head: str
    ancestors: frozenset[str]


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for statement in tree.body:
        value: ast.expr | None = None
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            value = statement.value
        elif isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            value = statement.value
        if value is not None:
            try:
                return ast.literal_eval(value)
            except (ValueError, TypeError) as error:
                raise InventoryFailure(
                    "migration graph contains dynamic metadata"
                ) from error
    raise InventoryFailure("migration graph metadata is incomplete")


def _migration_graph(root: Path) -> dict[str, tuple[str, ...]]:
    graph: dict[str, tuple[str, ...]] = {}
    for path in sorted((root / "migrations" / "versions").glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as error:
            raise InventoryFailure("migration graph cannot be read") from error
        revision = _literal_assignment(tree, "revision")
        down_revision = _literal_assignment(tree, "down_revision")
        if not isinstance(revision, str) or not revision or revision in graph:
            raise InventoryFailure("migration graph revision is ambiguous")
        if down_revision is None:
            parents: tuple[str, ...] = ()
        elif isinstance(down_revision, str):
            parents = (down_revision,)
        elif isinstance(down_revision, (tuple, list)) and all(
            isinstance(parent, str) and parent for parent in down_revision
        ):
            parents = tuple(down_revision)
        else:
            raise InventoryFailure("migration graph ancestry is invalid")
        graph[revision] = parents
    if not graph or any(
        parent not in graph for parents in graph.values() for parent in parents
    ):
        raise InventoryFailure("migration graph is incomplete")
    return graph


def _revision_ancestors(
    graph: Mapping[str, tuple[str, ...]],
    head: str,
) -> frozenset[str]:
    pending = [head]
    ancestors: set[str] = set()
    while pending:
        revision_id = pending.pop()
        if revision_id in ancestors:
            continue
        parents = graph.get(revision_id)
        if parents is None:
            raise InventoryFailure("migration graph is incomplete")
        ancestors.add(revision_id)
        pending.extend(parents)
    return frozenset(ancestors)


def _assert_acyclic_migration_graph(
    graph: Mapping[str, tuple[str, ...]],
) -> None:
    """Reject every cycle, including one reachable from an apparent head."""

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(revision_id: str) -> None:
        if revision_id in visiting:
            raise InventoryFailure("repository migration graph contains a cycle")
        if revision_id in visited:
            return
        parents = graph.get(revision_id)
        if parents is None:
            raise InventoryFailure("migration graph is incomplete")
        visiting.add(revision_id)
        for parent in parents:
            visit(parent)
        visiting.remove(revision_id)
        visited.add(revision_id)

    for revision_id in graph:
        visit(revision_id)


def repository_migration_contract(
    root: Path = ROOT,
) -> RepositoryMigrationContract:
    """Read the source migration graph without initializing the application."""

    graph = _migration_graph(root)
    _assert_acyclic_migration_graph(graph)
    parent_revisions = {parent for parents in graph.values() for parent in parents}
    heads = sorted(set(graph) - parent_revisions)
    if len(heads) != 1:
        raise InventoryFailure("repository migration head is ambiguous")
    head = heads[0]
    ancestors = _revision_ancestors(graph, head)
    if ancestors != frozenset(graph):
        raise InventoryFailure("repository migration graph is disconnected")
    return RepositoryMigrationContract(head=head, ancestors=ancestors)


def validate_production_database_url(database_url: str) -> None:
    """Require the canonical production endpoint before opening a socket."""

    try:
        parsed = urllib.parse.urlsplit(database_url)
    except ValueError as error:
        raise InventoryFailure("production database URL is invalid") from error
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise InventoryFailure("production database URL is invalid")
    if not parsed.username or not parsed.password or parsed.netloc.count("@") != 1:
        raise InventoryFailure("production database identity is invalid")
    try:
        canonical_host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as error:
        raise InventoryFailure("production database URL is invalid") from error
    if canonical_host != EXPECTED_DATABASE_HOST:
        raise InventoryFailure("production database host is unexpected")
    if port not in {None, 5432} or parsed.path != f"/{EXPECTED_DATABASE_NAME}":
        raise InventoryFailure("production database target is unexpected")
    try:
        pairs = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as error:
        raise InventoryFailure("production database URL options are invalid") from error
    query = dict(pairs)
    if (
        len(pairs) != len(query)
        or query
        != {
            "sslmode": "verify-full",
            "sslrootcert": LINUX_CA_BUNDLE,
        }
        or parsed.fragment
    ):
        raise InventoryFailure("production database URL options are unexpected")


def validate_migration_compatibility(
    observed_heads: Sequence[Any],
    contract: RepositoryMigrationContract,
) -> str:
    """Require one current source head containing all Session Prep ancestors."""

    if (
        len(observed_heads) != 1
        or not isinstance(observed_heads[0], str)
        or not observed_heads[0]
        or observed_heads[0] != contract.head
        or any(
            required not in contract.ancestors
            for required in REQUIRED_SESSION_PREP_ANCESTORS
        )
    ):
        raise InventoryFailure("production migration state is incompatible")
    return observed_heads[0]


def _observed_migration_heads(rows: Sequence[tuple[Any, ...]]) -> tuple[str, ...]:
    if any(len(row) != 1 or not isinstance(row[0], str) for row in rows):
        raise InventoryFailure("database migration state is malformed")
    return tuple(row[0] for row in rows)


def _count(value: Any) -> int:
    if isinstance(value, bool):
        raise InventoryFailure("invalid aggregate count")
    try:
        count = int(value)
    except (TypeError, ValueError) as error:
        raise InventoryFailure("invalid aggregate count") from error
    if count < 0 or count != value:
        raise InventoryFailure("invalid aggregate count")
    return count


def _fixed_counts(
    rows: Sequence[tuple[Any, ...]],
    *,
    labels: Sequence[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    allowed = set(labels)
    for row in rows:
        if len(row) != 2 or not isinstance(row[0], str) or row[0] not in allowed:
            raise InventoryFailure("aggregate query returned an invalid shape")
        if row[0] in counts:
            raise InventoryFailure("aggregate query returned a duplicate label")
        counts[row[0]] = _count(row[1])
    if set(counts) != allowed:
        raise InventoryFailure("aggregate query omitted a required label")
    return {label: counts[label] for label in labels}


def _parse_integrity(rows: Sequence[tuple[Any, ...]]) -> None:
    counts = _fixed_counts(rows, labels=INTEGRITY_CHECKS)
    if any(counts.values()):
        raise InventoryFailure("eligibility inventory state is ambiguous")


def _parse_classifications(
    rows: Sequence[tuple[Any, ...]],
) -> dict[str, int]:
    sparse: dict[str, int] = {}
    allowed = set(CLASSIFICATIONS)
    for row in rows:
        if len(row) != 2 or not isinstance(row[0], str) or row[0] not in allowed:
            raise InventoryFailure("eligibility query returned an invalid shape")
        if row[0] in sparse:
            raise InventoryFailure("eligibility query returned a duplicate label")
        sparse[row[0]] = _count(row[1])
    return {label: sparse.get(label, 0) for label in CLASSIFICATIONS}


def _observed_at_text(observed_at: datetime) -> str:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise InventoryFailure("observation timestamp must be timezone-aware")
    return (
        observed_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def build_report(
    classification_counts: Mapping[str, int],
    *,
    migration_head: str,
    observed_at: datetime,
    rollout_enabled: bool,
) -> dict[str, Any]:
    """Build one identifier-free, canonically digested inventory report."""

    if not isinstance(rollout_enabled, bool):
        raise InventoryFailure("rollout state is invalid")
    if not isinstance(migration_head, str) or not migration_head:
        raise InventoryFailure("migration head is invalid")
    if set(classification_counts) != set(CLASSIFICATIONS):
        raise InventoryFailure("classification set is invalid")
    counts = {label: _count(classification_counts[label]) for label in CLASSIFICATIONS}
    payload: dict[str, Any] = {
        "denial_reason_counts": {reason: counts[reason] for reason in DENIAL_REASONS},
        "eligible": counts["eligible"],
        "environment": EXPECTED_ENVIRONMENT,
        "migration_head": migration_head,
        "observed_at": _observed_at_text(observed_at),
        "rollout_enabled": rollout_enabled,
        "schema_version": REPORT_SCHEMA_VERSION,
        "total": sum(counts.values()),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["evidence_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def collect_inventory(
    connection: ReadOnlyConnection,
    *,
    observed_at: datetime,
    rollout_enabled: bool,
    migration_contract: RepositoryMigrationContract | None = None,
) -> dict[str, Any]:
    """Read aggregate eligibility state; never select or return identifiers."""

    validate_runtime_normalization_contract()
    _parse_integrity(connection.execute(INTEGRITY_QUERY).fetchall())
    contract = migration_contract or repository_migration_contract()
    migration_head = validate_migration_compatibility(
        _observed_migration_heads(connection.execute(MIGRATION_HEAD_QUERY).fetchall()),
        contract,
    )
    counts = _parse_classifications(
        connection.execute(
            ELIGIBILITY_QUERY,
            (observed_at, BAA_CURRENT_REFERENCE),
        ).fetchall()
    )
    return build_report(
        counts,
        migration_head=migration_head,
        observed_at=observed_at,
        rollout_enabled=rollout_enabled,
    )


def _rollout_enabled(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise InventoryFailure("rollout state is invalid")


def _verify_server(connection: ReadOnlyConnection) -> None:
    row = connection.execute(SERVER_IDENTITY_QUERY).fetchone()
    if row != (EXPECTED_DATABASE_NAME, EXPECTED_SCHEMA, "on"):
        raise InventoryFailure("production read-only boundary is unavailable")


def _verify_tls(connection: ReadOnlyConnection) -> None:
    """Confirm this verified-full libpq connection negotiated supported TLS."""

    row = connection.execute(TLS_STATUS_QUERY).fetchone()
    if row not in {(True, "TLSv1.2"), (True, "TLSv1.3")}:
        raise InventoryFailure("production verified TLS boundary is unavailable")


def main() -> int:
    if os.environ.get("PYTHON_ENV") != EXPECTED_ENVIRONMENT:
        print("Session Prep production eligibility preflight refused.", file=sys.stderr)
        return 2
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Session Prep production eligibility preflight failed.", file=sys.stderr)
        return 2

    try:
        validate_production_database_url(database_url)
        validate_runtime_normalization_contract()
        rollout_enabled = _rollout_enabled(
            os.environ.get("SESSION_PREP_PROVIDER_ENABLED")
        )
        observed_at = datetime.now(UTC)
        with psycopg.connect(
            database_url,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            application_name="session_prep_production_eligibility_preflight",
            options=(
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={STATEMENT_TIMEOUT_MILLISECONDS} "
                "-c timezone=UTC"
            ),
        ) as connection:
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            _verify_server(connection)
            _verify_tls(connection)
            report = collect_inventory(
                connection,
                observed_at=observed_at,
                rollout_enabled=rollout_enabled,
            )
            connection.rollback()
    except Exception:
        # Driver exceptions can include a DSN; state errors can be row-derived.
        # The command deliberately emits neither category of private detail.
        print("Session Prep production eligibility preflight failed.", file=sys.stderr)
        return 2

    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
