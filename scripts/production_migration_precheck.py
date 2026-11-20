#!/usr/bin/env python3
"""Read production migration state and fixed counts, without importing app."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, LiteralString

import psycopg

# Direct script execution and module execution use the same source-only graph
# reader. The eligibility module imports neither app nor migration modules.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import session_prep_production_eligibility_preflight as house  # noqa: E402

TRANSACTION: LiteralString = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)
BOUNDARY: LiteralString = """
SELECT current_database(), current_schema(),
       current_setting('transaction_read_only'),
       current_setting('transaction_isolation')
"""
HEADS: LiteralString = (
    "SELECT version_num FROM public.alembic_version ORDER BY version_num LIMIT 2"
)
GUARD_TABLES: LiteralString = """
SELECT to_regclass('public.scribe_capture_provider_link') IS NOT NULL,
       to_regclass('public.provider_practice_authority') IS NOT NULL
"""
LOCK_TIMEOUT_MILLISECONDS = 5_000
IDLE_TRANSACTION_TIMEOUT_MILLISECONDS = 60_000
# Match e78845cecca6's historical guard, including revoked links and PostgreSQL
# COUNT(DISTINCT)'s NULL semantics. Only the three integers leave the server.
ABORT_COUNTS: LiteralString = """
WITH historical AS (
    SELECT practice_id, scribe_tenant_id FROM public.scribe_capture_provider_link
)
SELECT
    (SELECT count(*) FROM (
        SELECT practice_id FROM historical GROUP BY practice_id
        HAVING count(DISTINCT scribe_tenant_id) <> 1
    ) p),
    (SELECT count(*) FROM (
        SELECT scribe_tenant_id FROM historical GROUP BY scribe_tenant_id
        HAVING count(DISTINCT practice_id) <> 1
    ) t),
    (SELECT count(*) FROM historical
     WHERE scribe_tenant_id = '00000000-0000-0000-0000-000000000000')
"""
NULL_PRACTICE_COUNT: LiteralString = """
SELECT count(*) FROM public.provider_practice_authority WHERE practice_id IS NULL
"""
COUNT_LABELS = ("practice_conflicts", "reused_uuids", "zero_uuids")
REFUSAL = "Production migration precheck refused; stop before promote."
PREFER_REFUSAL = (
    "Production migration precheck refused: sslmode=prefer is not allowed; "
    "supply an explicitly reviewed verify-full URL. Stop before promote."
)
REFUSAL_REASONS = frozenset(
    {
        "configuration_refused",
        "snapshot_refused",
        "migration_head_unknown",
        "pending_migrations",
        "guard_tables_missing",
        "e78845_abort",
    }
)


class PreferTLS(house.InventoryFailure):
    """The only separately rendered failure; contains no input values."""


def connection_parameters(database_url: str) -> dict[str, Any]:
    """Accept only an explicit production URL and operator-selected CA bundle."""
    parsed = urllib.parse.urlsplit(database_url)
    pairs = urllib.parse.parse_qsl(
        parsed.query, keep_blank_values=True, strict_parsing=True
    )
    if ("sslmode", "prefer") in pairs:
        raise PreferTLS()
    query = dict(pairs)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.username
        or not parsed.password
        or parsed.netloc.count("@") != 1
        or (parsed.hostname or "").lower() != house.EXPECTED_DATABASE_HOST
        or parsed.port not in {None, 5432}
        or parsed.path != "/production"
        or parsed.fragment
        or len(pairs) != len(query)
        or set(query) != {"sslmode", "sslrootcert"}
        or query["sslmode"] != "verify-full"
    ):
        raise house.InventoryFailure()
    ca = Path(query["sslrootcert"])
    if not ca.is_absolute() or not ca.is_file() or not os.access(ca, os.R_OK):
        raise house.InventoryFailure()
    # Reject ambient libpq routing/authentication overrides before connecting.
    # Do not pass through URL options, service files, or client TLS credentials; sslcertmode=disable stops libpq's default client-cert fallback.
    if any(key.startswith("PG") for key in os.environ):
        raise house.InventoryFailure()
    # sslcertmode=disable needs libpq 16+; refuse an older client library.
    if psycopg.pq.version() < 160000:
        raise house.InventoryFailure()
    return {
        "host": house.EXPECTED_DATABASE_HOST,
        "port": 5432,
        "dbname": "production",
        "user": urllib.parse.unquote(parsed.username),
        "password": urllib.parse.unquote(parsed.password),
        "sslmode": "verify-full",
        "sslrootcert": str(ca),
        "sslcertmode": "disable",
        "gssencmode": "disable",
        "connect_timeout": house.CONNECT_TIMEOUT_SECONDS,
        "application_name": "production_migration_precheck",
        "options": (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={house.STATEMENT_TIMEOUT_MILLISECONDS} "
            f"-c lock_timeout={LOCK_TIMEOUT_MILLISECONDS} "
            f"-c idle_in_transaction_session_timeout={IDLE_TRANSACTION_TIMEOUT_MILLISECONDS} "
            "-c search_path=public -c timezone=UTC"
        ),
    }


def integer_counts(row: tuple[Any, ...] | None, length: int) -> tuple[int, ...]:
    if (
        row is None
        or len(row) != length
        or any(type(n) is not int or n < 0 for n in row)
    ):
        raise house.InventoryFailure()
    return row


def pending_revisions(
    head: str,
    contract: house.RepositoryMigrationContract,
) -> list[str]:
    """List the checkout's pending revisions in deterministic dependency order."""
    if head not in contract.ancestors:
        return []  # unknown head is a separate refusal, never a clean result
    graph = house._migration_graph(house.ROOT)
    house._assert_acyclic_migration_graph(graph)
    if house._revision_ancestors(graph, contract.head) != contract.ancestors:
        raise house.InventoryFailure()
    applied = set(house._revision_ancestors(graph, head))
    remaining = set(contract.ancestors) - applied
    pending: list[str] = []
    while remaining:
        ready = sorted(
            revision for revision in remaining if set(graph[revision]) <= applied
        )
        if not ready:
            raise house.InventoryFailure()
        pending.extend(ready)
        applied.update(ready)
        remaining.difference_update(ready)
    return pending


def collect(
    connection: house.ReadOnlyConnection,
    contract: house.RepositoryMigrationContract,
) -> dict[str, Any]:
    # This is also checked when collect() is called independently of the CLI.
    if connection.execute(BOUNDARY).fetchone() != (
        "production",
        "public",
        "on",
        "repeatable read",
    ):
        raise house.InventoryFailure()
    house._verify_tls(connection)
    heads = connection.execute(HEADS).fetchall()
    if (
        len(heads) != 1
        or len(heads[0]) != 1
        or not isinstance(heads[0][0], str)
        or not re.fullmatch(r"[0-9a-f]{12}", heads[0][0])
    ):
        raise house.InventoryFailure()
    # Reject arbitrary head text rather than echoing it as operator evidence.
    head = heads[0][0]
    pending = pending_revisions(head, contract)
    reasons = []
    if head not in contract.ancestors:
        reasons.append("migration_head_unknown")
    if pending:
        reasons.append("pending_migrations")
    report = {
        "scope": "core",
        "migration_head": head,
        "repository_head": contract.head,
        "is_ancestor_of_repository_head": head in contract.ancestors,
        "pending_migrations": pending,
        "e78845cecca6_would_abort": None,
        "abort_counts": {},
        "provider_practice_authority_null_practice_id_count": None,
        "refusal_reasons": reasons,
    }
    tables = connection.execute(GUARD_TABLES).fetchone()
    if (
        tables is None
        or len(tables) != 2
        or any(type(value) is not bool for value in tables)
    ):
        raise house.InventoryFailure()
    if not all(tables):
        reasons.append("guard_tables_missing")
        return report
    try:
        abort = integer_counts(connection.execute(ABORT_COUNTS).fetchone(), 3)
        null_practice = integer_counts(
            connection.execute(NULL_PRACTICE_COUNT).fetchone(), 1
        )[0]
    except psycopg.errors.UndefinedTable:
        # A concurrent DROP may race the catalog observation. No further SQL
        # is issued on this failed transaction; main rolls it back. Discard
        # partial counts but preserve the already verified revision evidence.
        reasons.append("guard_tables_missing")
        return report
    report.update(
        {
            "e78845cecca6_would_abort": any(abort),
            "abort_counts": dict(zip(COUNT_LABELS, abort, strict=True)),
            "provider_practice_authority_null_practice_id_count": null_practice,
        }
    )
    if any(abort):
        reasons.append("e78845_abort")
    return report


def main() -> int:
    refusal_reason = "configuration_refused"
    try:
        parameters = connection_parameters(os.environ.get("DATABASE_URL", ""))
        refusal_reason = "snapshot_refused"
        contract = house.repository_migration_contract()
        with psycopg.connect(**parameters) as connection:
            connection.execute(TRANSACTION)
            report = collect(connection, contract)
            # End the one snapshot by rollback even though it is read-only.
            connection.rollback()
    except PreferTLS:
        print(f"{PREFER_REFUSAL} Reason: configuration_refused.", file=sys.stderr)
        return 2
    except Exception:
        # Driver exceptions can include credentials or row-derived text.
        # Never print the exception or a partial report.
        print(f"{REFUSAL} Reason: {refusal_reason}.", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return int(bool(report["refusal_reasons"]))


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(REFUSAL, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main())
