#!/usr/bin/env bash
# Synthetic Postgres for the r2 precheck tests. Local only, synthetic rows only, no PHI, no real secret.
# start: initdb + TLS (local test CA, cert for the prod hostname) + /etc/hosts maps that hostname to 127.0.0.1.
# stop:  stops the server and removes the /etc/hosts line again.
set -euo pipefail
PGB=/usr/lib/postgresql/17/bin
T=${PGT:-/workspace/r2-pgtest}
HN=cosmaneura-prod-postgres.postgres.database.azure.com
MARK="# r2-precheck-synthetic-test"
case "${1:-}" in
start)
  [ -f "$T/data/postmaster.pid" ] && "$PGB/pg_ctl" -D "$T/data" -m fast stop >/dev/null 2>&1 || true
  rm -rf "$T"; mkdir -p "$T/sock"
  "$PGB/initdb" -D "$T/data" -U postgres --auth=trust >/dev/null
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=r2-synthetic-test-ca" -keyout "$T/ca.key" -out "$T/ca.crt" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$HN" -keyout "$T/server.key" -out "$T/server.csr" 2>/dev/null
  printf 'subjectAltName=DNS:%s\n' "$HN" > "$T/san.ext"
  openssl x509 -req -in "$T/server.csr" -CA "$T/ca.crt" -CAkey "$T/ca.key" -CAcreateserial -days 2 -extfile "$T/san.ext" -out "$T/server.crt" 2>/dev/null
  chmod 600 "$T/server.key"
  cat >> "$T/data/postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = 5432
unix_socket_directories = '$T/sock'
ssl = on
ssl_cert_file = '$T/server.crt'
ssl_key_file = '$T/server.key'
ssl_min_protocol_version = 'TLSv1.2'
password_encryption = 'scram-sha-256'
log_min_error_statement = error
CONF
  cat > "$T/data/pg_hba.conf" <<HBA
local all all trust
hostssl all all 127.0.0.1/32 scram-sha-256
HBA
  "$PGB/pg_ctl" -D "$T/data" -l "$T/server.log" -w start >/dev/null
  grep -q "$MARK" /etc/hosts || echo "127.0.0.1 $HN $MARK" | sudo tee -a /etc/hosts >/dev/null
  psql -h "$T/sock" -U postgres -q -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE app_rw LOGIN PASSWORD 'SyntheticSentinelPw7f3a9c';
CREATE DATABASE production OWNER app_rw;
SQL
  psql -h "$T/sock" -U app_rw -d production -q -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
INSERT INTO alembic_version VALUES ('49126bf99c72');
CREATE TABLE scribe_capture_provider_link (practice_id text, scribe_tenant_id uuid);
CREATE TABLE provider_practice_authority (practice_id text);
CREATE TABLE doctor (id text PRIMARY KEY, email text, clerk_id text, deleted_at timestamptz);
CREATE TABLE practice (id text PRIMARY KEY, name text, baa_accepted_at timestamptz, negotiated_baa_on_file boolean NOT NULL DEFAULT false, subscription_status text);
CREATE TABLE baa_acceptance (id text PRIMARY KEY, practice_id text);
CREATE TABLE practice_member (id text PRIMARY KEY, practice_id text, doctor_id text, access_revoked_at timestamptz);
CREATE TABLE pilot_access_grandfather_grant (id text PRIMARY KEY, doctor_id text, revoked_at timestamptz);
-- synthetic rows only (fake names/emails that must NEVER appear in any output)
INSERT INTO doctor VALUES ('doc_1','leak-canary-1@example.test','user_canary1',NULL),('doc_2','leak-canary-2@example.test','user_canary2',NULL),('doc_3','leak-canary-3@example.test','user_canary3',now());
INSERT INTO practice VALUES ('prac_1','Canary Practice One',now(),false,NULL),('prac_2','Canary Practice Two',NULL,true,'active');
INSERT INTO baa_acceptance VALUES ('baa_1','prac_1');
INSERT INTO practice_member VALUES ('pm_1','prac_1','doc_1',NULL),('pm_2','prac_2','doc_2',NULL),('pm_3','prac_2','doc_3',now());
INSERT INTO pilot_access_grandfather_grant VALUES ('pg_1','doc_1',NULL),('pg_2','doc_2',now());
INSERT INTO scribe_capture_provider_link VALUES ('prac_1','11111111-1111-1111-1111-111111111111'),('prac_2','22222222-2222-2222-2222-222222222222');
INSERT INTO provider_practice_authority VALUES ('prac_1'),('prac_2');
SQL
  echo "synthetic pg up at $T (TLS, $HN -> 127.0.0.1)" ;;
stop)
  "$PGB/pg_ctl" -D "$T/data" -m fast stop >/dev/null 2>&1 || true
  sudo sed -i "/$MARK/d" /etc/hosts
  echo "synthetic pg stopped; /etc/hosts restored" ;;
*) echo "usage: $0 start|stop"; exit 2 ;;
esac
