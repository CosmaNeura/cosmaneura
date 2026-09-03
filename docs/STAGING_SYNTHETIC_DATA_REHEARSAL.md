# Staging synthetic-data inventory gate

Status: **READ-ONLY GATE IMPLEMENTED — LIVE PROOF NOT YET RUN**

This gate answers one narrow question before a staging restore or canonical promotion: does the complete staging data surface match an independently approved, synthetic-only state? The permitted state is more precisely described as **synthetic clinical data with allowlisted workforce PII**. It is not PII-free, and a passing result is not authorization to restore, sanitize, deploy, or change infrastructure. The versioned contract is `contracts/staging-rehearsal/v1/synthetic-data-policy.json`; the standalone reader is `scripts/staging_synthetic_inventory.py`. The reader does not import the application or Alembic and does not run migrations.

## Closed-world policy

The database contract covers every domain table exactly once. The pre profile pins Alembic revision `89c31f0ae275` and 62 tables. The post profile pins `9a2399212488` and 64 tables; only `practice_invitation` and `public_legal_acceptance` are added. Column name, PostgreSQL type, nullability, and ordinal position are exact. `public` must be the only non-system schema; any other non-system schema, sequence, or PostgreSQL large object fails. An unknown or missing table or column fails. `alembic_version` is a separate control table and must contain exactly the one profile revision.

Each domain table has one disposition:

- `required_empty`: clinical/free-text, vendor credential, webhook/inbox, transient session/token, Zoom, and other runtime state must contain no rows.
- `synthetic_lineage`: the approved patient, appointment, Preview-EHR, and synthetic-fixture state must match its keyed state digest, required synthetic markers, parent references, and root coverage.
- `workforce_allowlist`: exact keyed identity and full-state digests are approved separately. Credential and vendor-binding columns named by a null rule must be null. Schema-required join/access codes named by `rotation_columns` must be independently verified as rotated before their exact full-state digest is approved.
- `fixed_catalog`: exact keyed full-state digests are approved separately.

The blob account must contain exactly `uploads`, `example-notes`, `test-uploads`, and `test-example-notes`. Enumeration includes live blobs, versions, snapshots, deleted entries, and uncommitted blocks. Any uncommitted block fails because its bytes cannot be independently read; the first two containers must be empty, and the test containers must match an independently approved fixed state.
For every retained object, the gate streams and hashes its bytes plus metadata, tags, content type, version/snapshot identity, and deletion state. An unknown container, unreadable object version, or object-state change fails. Two full listings must produce identical digests so a concurrent write cannot yield a passing torn inventory.

Redis v1 is intentionally stricter than a cache classification: workers and schedulers must be quiesced and database 0 must be selected for the connection. Two complete `INFO keyspace` summaries must agree and report zero keys across every logical database. The reader never scans or prints keys. Any key in any logical database, malformed aggregate response, or changing count fails.

## Independent approved state

The policy describes structure and rules. A second, access-restricted JSON file binds the exact retained state without storing raw rows or object names:

```json
{
  "schema_version": 1,
  "policy_sha256": "<64 lowercase hex>",
  "profile": "post",
  "tables": {"doctor": {"count": 1, "state_hmac": "<64 lowercase hex>",
    "identity_hmac": "<64 lowercase hex>"}},
  "containers": {
    "test-uploads": {"count": 0, "state_hmac": "<64 lowercase hex>"}
  }
}
```

The real file must contain every and only retained table/container required by the selected profile. It may contain counts and HMAC-SHA-256 values only. A privacy-authorized reviewer must derive and approve it independently using the same high-entropy key as the verification run. Do not place the approved-state file or HMAC key in the repository. A digest copied from an unreviewed failing run is not approval.

## Read-only run

Prerequisites remain external blockers: named operator and observer, workload quiescence, a protected evidence destination, approved source/candidate SHAs, and a reviewed approved-state file. Set the database and Redis URLs through environment variables so credentials do not enter argv or shell history.

```text
umask 077
export STAGING_DATABASE_URL='<secret URL>'
export STAGING_REDIS_URL='<secret URL>'
python scripts/staging_synthetic_inventory.py \
  --policy contracts/staging-rehearsal/v1/synthetic-data-policy.json \
  --approved-state /protected/path/approved-state.json \
  --profile post \
  --source-sha <40-hex-source-sha> \
  --candidate-sha <40-hex-candidate-sha> \
  --hmac-key-file /protected/path/inventory-hmac.key \
  --blob-account-url https://cosmaneurastaginguploads.blob.core.windows.net \
  --redis-quiesced > /protected/path/inventory-evidence.json
```

The database URL is accepted only for the pinned staging database with `sslmode=verify-full` and an absolute `sslrootcert`. Redis is accepted only as `rediss` for the pinned staging cache, port 6380, database 0, with `ssl_cert_reqs=required`, `ssl_check_hostname=true`, and an absolute CA path. Optional TLS client private keys and the HMAC key must be owner-only regular files with mode `0600`; symlinks and unsafe parent directories are rejected. The command opens PostgreSQL as repeatable-read/read-only, performs no writes, and emits a single JSON object. Evidence is limited to policy/source/candidate identity, aggregate counts, rule-violation counts, and keyed catalog/state digests. It never emits database values, emails, object names, Redis keys, tokens, or connection strings. Exit 0 means every check passed; exit 1 means a policy mismatch; exit 2 means validation/provider failure. Error codes are bounded and contain only policy-defined identifiers. `source_sha` and `candidate_sha` are validated 40-hex operator claims in this standalone phase; the reader cannot prove repository provenance. A later trusted workflow must bind both values to its protected checkout and restore record before this evidence can authorize a rehearsal.

## Promotion boundary

A pass is necessary but not sufficient for the rehearsal. Before any restore or canonical staging cutover, separately approve and prove the isolated restore network/identity/TTL envelope, deterministic sanitizer, evidence sink, cleanup authority, RTO/RPO, and post-sanitize rerun. A separate version-bound schema descriptor must also cover defaults, generated/identity expressions, constraints, indexes, user triggers and their function bodies, RLS policies, enum labels, and view/function definitions; the current gate deliberately does not represent its exact table/column check as a complete schema fingerprint. The current live database and Redis connection material must first be upgraded from certificate-optional or `sslmode=require` behavior to the verified TLS forms required here. No live inventory has been collected by this change.

## Dormant isolated PostgreSQL restore envelope
Status: **IMPLEMENTED BUT DELIBERATELY NON-EXECUTABLE**. The protected-master workflow at `.github/workflows/staging_restore_rehearsal.yml`, controller at `scripts/staging_restore_envelope.py`, and pinned policy at `contracts/staging-rehearsal/v1/restore-envelope-policy.json` form the next Tier-2 gate. They do not alter the canonical staging deployment workflow or persistent staging infrastructure. No Azure operation or workflow dispatch has been performed.
The current staging source is public-access mode with two pinned firewall rules, including Azure's `0.0.0.0` service rule. Azure CLI point-in-time restore inherits public mode, so this design does not claim a direct private restore. Before any run, an authorized infrastructure change must create the exact dedicated `cosmaneura-staging-rehearsal` resource group and assign the pinned deny policy that rejects every `Microsoft.DBforPostgreSQL/flexibleServers/firewallRules` write.
Provision verifies the exact tenant, subscription, source metadata/firewalls, empty target group, OIDC client/principal, every effective target/source role assignment and canonical role-permission digest, the sole federated subject, deny assignment/`All`-mode rule digest, and absence of policy modifiers or exemptions. It restores to a controller-derived expiry-bearing name; immediately proves zero inherited target firewall rules through ARM, disables and rechecks public access, then creates the outbound-disabled, unpeered VNet and single private-endpoint subnet with no routes, NAT, NSG, delegations, service endpoints/policies, or custom DNS, plus deterministic private endpoint/NIC and private DNS zone/link/group. It rejects public IPs, unknown/missing resources, and tag/name drift; creates no data-plane identity/client; and copies neither Blob nor Redis. Azure manages the private-endpoint NIC, so it is not mutated for tagging; its exact identity and ownership are instead bound through the private endpoint's target and NIC references.
The aggregate owner-only `restore-envelope.json` and `SHA256SUMS` bind policy/repository/protected workflow commit/run/attempt, source-inventory and quiescence digests, fixed `pre`/`89c31f0ae275` profile, source/candidate SHAs, exact source/target/network IDs, provisioner IDs, creation/expiry, and isolation counts. GitHub artifact provenance signs the receipt. No credentials, DSNs, rows, object names, Redis keys, or provider error text are emitted. The checked-in `trusted_evidence_binding_implemented: false` independently blocks provision: the current CLI accepts digest/SHA claims but does not retrieve and authenticate the approved inventory, prove its pass/profile/database identity, bind its candidate to the checkout, or bind its quiescence record and restore point. A reviewed trusted evidence handoff must implement those checks before setting the flag true.
The default TTL is six hours and the maximum is 24. The separate cleanup identity handles manual, failed-run, and hourly expired cleanup; the schedule stays off unless `STAGING_RESTORE_CLEANUP_ENABLED` is exactly `true`. Its environment must permit protected-master schedules without human review while restricting deployment branches to protected `master`; the provision environment requires founder review and the same branch restriction. Credentialless authorization validates protected repo/ref, founder actor and rerun actor for manual runs, action, and configured policy before Azure login, then each environment job rechecks the current protected head immediately before login. Manual run IDs include the run attempt so a failed rerun cannot clean up its prior successful attempt. Cleanup revalidates the exact tenant/subscription/client-to-principal/target group but deliberately does not block safe deletion on later RBAC, FIC, or deny-policy drift; exact names, ownership tags, encoded expiry, and PE-to-target/NIC bindings gate deletion. Only failed-provision compensation may tolerate a missing or partially tagged current-attempt set. Unknown/malformed/orphan/multiple sets stop cleanup before deletion. No deployment-stack ownership is claimed.
### Required activation inputs
The exact tenant/subscription/source values are pinned from read-only reconciliation. Every remaining `CONFIGURE_EXACT_...` value deliberately blocks execution until a reviewed setup change supplies provision/cleanup client and principal IDs, exact role IDs and permission digests, subscription-scoped read-only control roles, deny-policy definition ID/rule digest, and updates the pinned policy SHA. The identities also need the minimum Microsoft Graph application-read authority required for the SP/FIC self-checks. The pre-existing target group, deny assignment, branch-restricted environments, OIDC bindings, evidence sink, operator, observer, cleanup approver, secondary contact, and approved RTO/RPO remain prerequisites. Before activation, the trusted workflow must inventory every database restored with the server (not only `staging`) and close the Azure CLI long-running-restore timeout race so failed-run cleanup cannot miss a still-materializing public-mode target; the hourly sweeper is defense in depth, not proof of immediate cleanup. Sanitization, migration, application access, and staging cutover remain separate approvals; an envelope pass authorizes none of them.
