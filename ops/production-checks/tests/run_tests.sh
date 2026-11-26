#!/usr/bin/env bash
# Runs the synthetic-only test suite: fresh local Postgres (TLS, synthetic rows), runner tests, teardown.
# Needs: postgresql 17 server binaries, openssl, sudo (for the temporary /etc/hosts line), a python with psycopg[binary].
set -uo pipefail
cd "$(dirname "$0")"
PY=${PY:-/workspace/r2-venv/bin/python}
./synthetic_pg.sh start || exit 1
echo "python: $("$PY" --version 2>&1); psycopg $("$PY" -c 'import psycopg;print(psycopg.__version__, "libpq", psycopg.pq.version())'); postgres $(/usr/lib/postgresql/17/bin/postgres --version | awk '{print $3}')"
"$PY" test_runner.py; rc=$?
./synthetic_pg.sh stop
exit $rc
