#!/usr/bin/env python3
"""Pin the resolved staging deployment DSN to hostname-verifying Linux TLS.

The staging deployment template captures stdout as a credential-bearing value.
This helper resolves no secrets, contacts no service and performs no deployment.
Errors are deliberately independent of the supplied connection string.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

STAGING_HOST = "cosmaneura-staging-postgres.postgres.database.azure.com"
LINUX_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def has_control_characters(value: str) -> bool:
    return any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in value)


def normalize_staging_deployment_database_url(value: str) -> str:
    """Strengthen only the exact staging target; reject ambiguous connections."""
    if (
        not value
        or len(value) > 8192
        or has_control_characters(value)
        or any(c.isspace() for c in value)
        or re.search(r"%(?![0-9a-fA-F]{2})", value)
    ):
        raise ValueError("staging_database_tls_contract")
    try:
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        query = dict(pairs)
        if (
            parsed.scheme not in {"postgresql", "postgresql+psycopg"}
            or parsed.hostname != STAGING_HOST
            or parsed.port != 5432
            or parsed.path != "/staging"
            or parsed.fragment
            or not parsed.username
            or not parsed.password
            or len(pairs) != len(query)
            or set(query) - {"sslmode", "sslrootcert"}
            or query.get("sslmode") not in {"require", "verify-full"}
            or query.get("sslrootcert", LINUX_CA_BUNDLE) != LINUX_CA_BUNDLE
            or any(
                has_control_characters(unquote(item, errors="strict"))
                for item in (parsed.username, parsed.password)
            )
        ):
            raise ValueError("staging_database_tls_contract")
        return urlunsplit(
            parsed._replace(
                query=urlencode(
                    {"sslmode": "verify-full", "sslrootcert": LINUX_CA_BUNDLE}
                )
            )
        )
    except ValueError:
        raise ValueError("staging_database_tls_contract") from None


def main() -> int:
    try:
        normalized = normalize_staging_deployment_database_url(
            os.environ.get("DATABASE_URL", "")
        )
    except ValueError:
        print("staging_database_tls_contract", file=sys.stderr)
        return 2
    print(normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
