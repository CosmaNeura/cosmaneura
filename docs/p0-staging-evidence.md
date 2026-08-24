# P0 staging evidence capture

`scripts/p0_staging_evidence.py` is a read-only, staging-only evidence tool. It
accepts only the reviewed staging PostgreSQL host, the `staging` database, and
the two query parameters `sslmode=verify-full` and `sslrootcert=<absolute-path>`.
It then verifies the connected database, TCP server, and SSL session before
reading any evidence rows. It never imports the application or runs migrations.

`verify-full` is mandatory: it verifies both the trusted certificate chain and
the exact Azure staging hostname. `require`, `prefer`, `allow`, and `disable`
are rejected. The `sslrootcert` value must name a readable PEM trust bundle
from a reviewed local trust source. The path must be absolute and canonical,
with no symlink component; the file and its parent must be owned by root or the
operator and must not be group- or world-writable. Missing, empty, non-PEM, and
duplicate or additional connection parameters fail closed. On macOS, use the
canonical `/private/etc/ssl/cert.pem` path rather than the `/etc` symlink. Do
not download or trust a certificate presented by the database endpoint itself.

Do not put a workstation-specific CA path into the shared 1Password database
item: that same value is deployed to the application containers. Instead, the
capture subshell below accepts only the currently reviewed `sslmode=require`
suffix and replaces it in memory with these evidence-only query parameters for
this operator host:

```text
sslmode=verify-full&sslrootcert=%2Fprivate%2Fetc%2Fssl%2Fcert.pem
```

The complete URL must never be printed; it contains a database credential. If
capture runs on another host, use that host's reviewed, canonical CA bundle
path and re-run the file-safety checks rather than copying an unreviewed root
into place.

The version-2 `pre` phase requires all six P0 tables to be absent. The exceptional
`pre-present-empty` phase requires all six tables to be present and every one of
them to be empty; use it only when an independently verified migration has already
rolled the schema forward without writing P0 state. The `post` phase requires all
six tables to be present. Any partial schema, unreadable table, non-empty
`pre-present-empty` table, unexpected database target, or unexpected connected-
server identity fails closed with a sanitized message.

Do not import `app.*` while staging configuration is loaded before the PRE capture.
Application setup runs Alembic in deployed environments, so even a read-only
helper that imports an application model can advance the staging schema.

## Protected local capture

Use one randomly generated binary HMAC key for both captures. Evidence contains
no raw rows, but its exact small-cohort counts and keyed state digests are still
sensitive operational data. Do not display it, use `tee`, send it to CI logs, or
commit it.

From the repository root:

```sh
evidence_dir="$(mktemp -d /private/tmp/cosmaneura-p0-staging-evidence.XXXXXX)"
chmod 700 "$evidence_dir"

(
  umask 077
  if [ ! -e "$evidence_dir/hmac.key" ]; then
    openssl rand -out "$evidence_dir/hmac.key" 32
  fi
  test ! -L "$evidence_dir/hmac.key"
  test "$(stat -f '%Lp' "$evidence_dir/hmac.key")" = 600
  test "$(stat -f '%z' "$evidence_dir/hmac.key")" = 32
  pre_candidate="$evidence_dir/pre.candidate.json"
  pre_stderr_candidate="$evidence_dir/pre.candidate.stderr"
  env -i HOME="$HOME" PATH="$PATH" OP_CACHE=false \
    RENDER_DIRENV=.env.staging.backend \
    direnv exec . /bin/sh -eu -c '
      case "$DATABASE_URL" in
        *"?sslmode=require") ;;
        *) exit 2 ;;
      esac
      DATABASE_URL="${DATABASE_URL%\?sslmode=require}?sslmode=verify-full&sslrootcert=%2Fprivate%2Fetc%2Fssl%2Fcert.pem"
      export DATABASE_URL
      exec .venv/bin/python scripts/p0_staging_evidence.py \
        --phase pre \
        --hmac-key-file "$1"
    ' p0-staging-evidence "$evidence_dir/hmac.key" \
      >"$pre_candidate" \
      2>"$pre_stderr_candidate"

  chmod 600 "$pre_candidate" "$pre_stderr_candidate"
  jq -e '.environment == "staging" and .capture_phase == "pre" and
    .schema_version == 2 and .p0_schema.expected_table_count == 6 and
    .p0_schema.present_count == 0 and
    (.p0_schema.presence | all(.[]; . == false))' \
    "$pre_candidate" >/dev/null
  mv -f "$pre_candidate" "$evidence_dir/pre.json"
  mv -f "$pre_stderr_candidate" "$evidence_dir/pre.stderr"
)
```

The candidate files are deliberate: a failed retry must never truncate a valid
canonical PRE. If the schema has already been independently verified as all-
present and all-empty, use `--phase pre-present-empty` instead and require
`.capture_phase == "pre-present-empty"`, `.p0_schema.present_count == 6`, and
`all(.p0_schema.presence[]; . == true)` before the atomic rename. Do not use that
mode merely to bypass a failed ordinary `pre` capture.

The clean environment is important: `.env.staging.backend` supplies
`PYTHON_ENV` and `DATABASE_URL` together from the staging 1Password vault, so a
production URL inherited from an operator shell cannot be paired accidentally
with the staging marker.

After the reviewed staging deployment and migrations, repeat the command with
`--phase post` and write to `post.json`, reusing the same `hmac.key`. Keep the
same `umask 077` and clean-environment wrapper.

Before the deployment, copy
`docs/p0-staging-evidence-expectations.example.json` to the protected evidence
directory as `expected.private.json`. Replace every `-1` with the exact
aggregate count from the reviewed continuity/capability plan. Put the exact
plan SHA-256 into each corresponding array once per row that plan will create;
repeated rows require repeated hashes. Keep every view-as count and both
superadmin revocation counts at zero. Run this comparison after migrations and
reviewed grants are applied but before any view-as smoke test. A post-smoke
capture is supplemental evidence; it does not replace this pre-smoke gate.
Never include an email address, Clerk subject, Doctor ID, or other identity
value.

Validate and compare without printing either snapshot:

```sh
jq -e '.environment == "staging" and
  (.capture_phase == "pre" or .capture_phase == "pre-present-empty") and
  ((.capture_phase == "pre" and .p0_schema.present_count == 0) or
   (.capture_phase == "pre-present-empty" and
    .p0_schema.present_count == .p0_schema.expected_table_count))' \
  "$evidence_dir/pre.json" >/dev/null

jq -e '.environment == "staging" and .capture_phase == "post" and
  .p0_schema.present_count == .p0_schema.expected_table_count' \
  "$evidence_dir/post.json" >/dev/null

jq -e -s '.[0].key_fingerprint_hmac_sha256 ==
  .[1].key_fingerprint_hmac_sha256' \
  "$evidence_dir/pre.json" "$evidence_dir/post.json" >/dev/null

chmod 600 "$evidence_dir/expected.private.json"
.venv/bin/python scripts/p0_staging_evidence_compare.py \
  --pre "$evidence_dir/pre.json" \
  --post "$evidence_dir/post.json" \
  --expectations "$evidence_dir/expected.private.json" \
  --hmac-key-file "$evidence_dir/hmac.key"
```

The comparator authenticates both snapshots with the private HMAC key, checks
the all-absent to all-present schema transition, and fails unless BAA
acceptance/version, doctor onboarding, practice authorization/billing,
membership authorization, organization/roster, identity-recovery state, and
stable provider-account bindings, and the original continuity cohort are
unchanged. Rotating provider tokens and token-expiry timestamps are excluded.
An approved omission amendment
may add grants, so the snapshot separately identifies base and amendment-linked
grants; the base must remain byte-for-byte stable. New amendment and
superadmin rows must match both the reviewed aggregate counts and the
hash-confirmed plan multiset. Cross-environment rows, safe-prelogin amendments,
or an unended view-as session fail the comparison.

Do not require the top-level snapshot HMACs to match. The expected schema
transition and reviewed P0 additions change that HMAC. Normal operational
fields such as `last_active_at`, access audit events, and provider-token state
are intentionally excluded from the unchanged projections; authorization and
business-state fields are not.
