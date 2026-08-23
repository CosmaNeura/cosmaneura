#!/usr/bin/env python3
"""Count duplicate groups that would block the Stripe hardening migration.

This entrypoint is intentionally outside the ``app`` package. Importing
``app`` initializes production services and may run Alembic before a CLI
command executes, which would defeat a pre-migration check.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, LiteralString, Protocol

import psycopg

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MILLISECONDS = 15_000

STRIPE_COLUMN_PRESENCE_QUERY: LiteralString = """
SELECT count(*) FILTER (WHERE column_name = 'stripe_customer_id'),
       count(*) FILTER (WHERE column_name = 'stripe_subscription_id')
  FROM information_schema.columns
 WHERE table_schema = current_schema()
   AND table_name = 'practice'
   AND column_name IN ('stripe_customer_id', 'stripe_subscription_id')
"""

STRIPE_DUPLICATE_GROUP_QUERIES: tuple[tuple[str, LiteralString], ...] = (
    (
        "duplicate_stripe_customer_id_groups",
        """
        SELECT count(*)
          FROM (
                SELECT 1
                  FROM practice
                 WHERE stripe_customer_id IS NOT NULL
                 GROUP BY stripe_customer_id
                HAVING count(*) > 1
               ) AS duplicate_groups
        """,
    ),
    (
        "duplicate_stripe_subscription_id_groups",
        """
        SELECT count(*)
          FROM (
                SELECT 1
                  FROM practice
                 WHERE stripe_subscription_id IS NOT NULL
                 GROUP BY stripe_subscription_id
                HAVING count(*) > 1
               ) AS duplicate_groups
        """,
    ),
)

BAA_DUPLICATE_GROUP_QUERY: LiteralString = """
SELECT count(*)
  FROM (
        SELECT 1
          FROM baa_acceptance
         GROUP BY practice_id, baa_version_id
        HAVING count(*) > 1
       ) AS duplicate_groups
"""


class PreflightFailure(RuntimeError):
    """A sanitized preflight failure whose details are never emitted."""


class QueryResult(Protocol):
    def fetchone(self) -> tuple[Any, ...] | None: ...


class ReadOnlyConnection(Protocol):
    def execute(self, query: LiteralString) -> QueryResult: ...


def collect_duplicate_group_counts(
    connection: ReadOnlyConnection,
) -> dict[str, int]:
    """Return aggregate group counts without selecting duplicate values."""

    presence_row = connection.execute(STRIPE_COLUMN_PRESENCE_QUERY).fetchone()
    if presence_row is None or len(presence_row) != 2:
        raise PreflightFailure("column-presence query returned an invalid shape")
    try:
        presence = tuple(int(value) for value in presence_row)
    except (TypeError, ValueError) as error:
        raise PreflightFailure(
            "column-presence query returned invalid counts"
        ) from error
    if any(value not in (0, 1) for value in presence):
        raise PreflightFailure("column-presence query returned invalid counts")

    counts: dict[str, int] = {
        "duplicate_stripe_customer_id_groups": 0,
        "duplicate_stripe_subscription_id_groups": 0,
    }
    for column_present, (label, statement) in zip(
        presence, STRIPE_DUPLICATE_GROUP_QUERIES, strict=True
    ):
        if not column_present:
            continue
        row = connection.execute(statement).fetchone()
        if row is None or len(row) != 1:
            raise PreflightFailure("aggregate query returned an invalid shape")
        try:
            count = int(row[0])
        except (TypeError, ValueError) as error:
            raise PreflightFailure(
                "aggregate query returned an invalid count"
            ) from error
        if count < 0:
            raise PreflightFailure("aggregate query returned an invalid count")
        counts[label] = count

    baa_row = connection.execute(BAA_DUPLICATE_GROUP_QUERY).fetchone()
    if baa_row is None or len(baa_row) != 1:
        raise PreflightFailure("aggregate query returned an invalid shape")
    try:
        baa_count = int(baa_row[0])
    except (TypeError, ValueError) as error:
        raise PreflightFailure("aggregate query returned an invalid count") from error
    if baa_count < 0:
        raise PreflightFailure("aggregate query returned an invalid count")
    counts["duplicate_baa_acceptance_practice_version_groups"] = baa_count
    return counts


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("billing migration preflight failed", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=(
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={STATEMENT_TIMEOUT_MILLISECONDS}"
            ),
        ) as connection:
            counts = collect_duplicate_group_counts(connection)
            connection.rollback()
    except (psycopg.Error, PreflightFailure):
        # Connection exceptions can contain a DSN. Never print exception text.
        print("billing migration preflight failed", file=sys.stderr)
        return 2

    print(json.dumps(counts, sort_keys=True, separators=(",", ":")))
    return 1 if any(counts.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
