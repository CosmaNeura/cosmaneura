# Staging PostgreSQL server-scope evidence

`scripts/staging_server_scope_evidence.py` is a dormant, read-only prerequisite for a complete restore rehearsal. It closes the gap left by a single-database schema inventory: PostgreSQL point-in-time restore operates at server scope. The collector enumerates every `pg_database` row, including templates and platform/system databases. Policy names exactly one non-template, connectable primary database whose keyed server-scope receipt is explicitly bound to the separate full row inventory. Every other connectable database must have no user relations (tables, partitioned tables, views, materialized views, sequences, or foreign tables) and no large objects; this stronger invariant proves that no secondary database can carry unsanitized user rows. A disabled database is allowed only when it is a template. Any unknown database, disabled non-template database, non-primary user relation or large object, connection failure, or catalog permission failure blocks evidence.

Every connectable database yields a keyed schema fingerprint plus exact keyed inventories for user relations; normalized shared dependencies; extensions; foreign-data wrappers, servers, tables, options and ACLs; column ACLs/options; language ACLs; user mappings; table and PostgreSQL 16 schema publication membership; subscription worker/relation state; security labels; event triggers; and large-object metadata, ACLs, and content pages. Schema fingerprints include normalized `pg_rewrite` rule definitions and state, resolved effective relation tablespaces, extension owners, custom collation owners and locale properties, and view `security_invoker`/`security_barrier` options.

Server evidence pins `pg_authid` roles/attributes and keyed password-verifier state (presence and scheme remain inside the role aggregate), memberships, per-role/database settings, database locale/owners/ACLs/default tablespaces, tablespace locations/options/ACLs, subscription definitions, prepared transactions, replication slots (including WAL status and restart/confirmed LSNs), replication origins/status, parameter ACLs, shared descriptions and security labels, and all visible `pg_settings`. Subscription `subdbid` is resolved to database name before hashing; `subskiplsn = 0/0` is canonical unset. Relation `srsublsn = 0/0` is likewise unset, while a nonzero value is captured exactly as the copy/synchronization progress boundary. New, missing, unreadable, or changed records fail closed. Numeric catalog OIDs are excluded as allocator artifacts, except large-object IDs whose referential identity must survive restore. Per-database `pg_shdepend` edges are retained as resolved object-address tuples, so owners, ACL role references, tablespaces and other shared dependencies are covered without hashing allocator OIDs. `pg_prepared_xacts`, tablespace locations, `pg_shdescription`, and replication origins are also pinned. Role and object names are policy-significant.

Raw identities, settings, password verifiers, FDW/user-mapping/subscription options, LSNs, security labels, shared descriptions, rewrite definitions, shared-dependency object names, tablespace locations, and large-object pages remain only in process memory. Evidence contains counts and domain-separated HMAC-SHA-256 digests and binds the HMAC of source resource identity, the primary database identity, source revision, capture interval, policy digest, and owner-controlled key version. The key file must be an owner-only regular file and must never be committed.

`scripts/validate_staging_evidence_manifest.py` now accepts this collector's
real receipt shape as `server-scope.json`. The manifest binds its exact file
bytes and separately binds the SHA-256 of canonical receipt JSON; the receipt
does not acquire an invented embedded receipt digest. Validation requires the
exact server/database surface sets, canonical identity-HMAC database ordering,
one primary database, zero user-relation and large-object counts for every
database marked `empty`, at most 64 databases, at most 100,000 records per
surface, and at most 500,000 records across the receipt. The receipt policy
digest and source revision must match the manifest, and its capture interval
must fall inside the manifest observation's 15-minute freshness bracket.

The connection string is accepted only from `STAGING_SERVER_SCOPE_DATABASE_URL`; credentials in command arguments are unsupported. Production policy and capture require one lowercase Azure PostgreSQL FQDN ending in `.postgres.database.azure.com`. Production capture accepts only explicit `host`, `port`, `dbname`, `user`, `password`, `sslmode=verify-full`, and `sslrootcert=system` libpq parameters; the sanitized connection also disables client certificates and GSS encryption so ambient identity material and GSS negotiation cannot alter authentication or bypass TLS hostname verification. Alternate routing, service files, session options, client TLS overrides, multi-host targets, ambient libpq `PG*` connection variables, and ambient OpenSSL trust-path/configuration overrides fail closed. The adapter discards the original DSN after parsing and connects with a sanitized parameter map. `allow_local=True` remains a test-only code path for an exact loopback policy target and is never exposed by the CLI.

Named server cursors enforce per-query and aggregate record/byte ceilings while iterating. Before cursor creation, a server-side `octet_length(row_to_json(q)::text)` aggregate measures the rendered, uncompressed form. Its byte totals are conservatively multiplied by six to bound Python's `ensure_ascii=True` expansion; consequently the effective preflight ceiling can be one sixth of the nominal canonical byte limit. The streaming loop independently checks each exact canonical record before appending or incrementing counters. Queries contain no unbounded `array_agg`, and a connection cancellation timer enforces the monotonic 15-minute total deadline. A breached limit fails closed without partial evidence.

Every connectable database is captured twice in deterministic order and both keyed surface sets must match; server surfaces must also match before and after both passes. Activation must run during a coordinated freeze of DDL, role/ACL, publication/subscription, prepared-transaction, and replication-state changes so the two-pass bracket is a stable cross-database capture.

The committed policy intentionally has `activation_ready: false` and exact
configuration placeholders, so the collector command refuses before
connecting. The local manifest validator can validate the receipt contract in
that dormant state, but its output remains `activation_state: blocked` with
`server_scope_policy_unconfigured`, attestation, provider-access, workflow, and
restore-activation blockers. When a reviewed policy becomes active, the
validator additionally requires exact revision, key-version, server-surface,
database-status, and database-surface agreement with that policy.

`scripts/authenticate_staging_server_scope_receipt.py` is the next dormant,
offline boundary. Its supported `authenticate_evidence_bundle_with_binding`
handoff entry point loads all three exact hash-pinned checked-in policies and
returns both the public result and opaque partial; `authenticate_evidence_bundle`
is the compatibility projection that discards the partial. Both entry points
refuse while either checked-in policy is disabled, validate the closed-world
bundle themselves, and pass the resulting opaque one-read snapshot only to private,
dependency-injected helpers used by unit tests. Callers cannot replace the
canonical policy bytes through the supported interface, and the snapshot cannot
be constructed through its public class interface. The helper reparses the immutable result and
`server-scope.json` bytes, while the command reads the collector HMAC key
from an absolute owner-owned mode-`0600` regular file, and calls the collector's
shared verifier for the original domain and length framing. The verifier
constant-time checks the source-resource, primary-database, and whole-receipt
HMACs and requires every server/database pin to match an active, configured
server-scope policy. Both policy mappings are parsed from the exact raw bytes
named by their SHA-256 digests, and the authentication policy pins the same
server-policy byte digest used by the verifier; byte subclasses are rejected so
hashing and parsing cannot observe different content. The server policy's source
resource and primary database must also equal the authentication policy's source.
The authentication receipt binds the manifest, checksum
index, evidence set, exact server-scope bytes and canonical receipt, source
revision/resource/database, key version, observation/expiry window, and trusted
authentication time. That time is sampled after HMAC verification and is used
for both the final freshness decision and `authenticated_at`; the HMAC-bound
capture interval must end no later than the manifest observation and begin no
more than 15 minutes before it. Key bytes include
every byte of the key file, including a
trailing newline, and are never stripped, emitted, or logged.
The CLI likewise never prints the authenticated receipt: a successful future
run requires `--output-file` and creates that file once with owner-only
mode `0600`, preventing receipt HMACs and resource identifiers from entering
job logs.

The same private authentication transaction now also mints an opaque,
immutable `ServerAuthenticatedSafeSubmitBinding` from the validator-owned
snapshot and the freshly derived authentication-receipt digest. Its canonical
bytes contain exactly the manifest evidence-binding fields plus
`server_scope_authentication_receipt_sha256`; provider and GitHub-attestation
receipt fields are intentionally absent. Mutable projections cannot change the
sealed bytes, the object retains no key, and its representation reveals no
receipt or HMAC. It is an in-process partial handoff only: it cannot be decoded
back into trusted state, converted to `RestoreEvidenceBinding`, or used to mint
a `RestoreRequest`.

The final `RestoreEvidenceBinding` accepted by the safe-submit model is now
sealed as well. Direct construction, subclasses, raw mappings, unsealed
instances, and the valid server-authenticated partial all fail before marker or
ARM access. No production minter exists yet; a later composite verifier must
require independently sealed provider-observation and GitHub-attestation
outputs alongside this partial before it can create the final binding.
These module-identity guards prevent deserialized or accidental type
substitution inside trusted executor code; they are not a sandbox against
arbitrary Python code already executing in the process and must never replace
cryptographic receipt verification at the future composite boundary.

`source-provider-observation-policy.json` and
`scripts/verify_staging_source_provider_observation.py` define the next dormant
handoff. The verifier accepts only the exact sealed server-authenticated
partial, snapshots its canonical bytes once, and binds their SHA-256 into an
immutable provider receipt. A narrow provider interface permits one logical
GET of the pinned Azure PostgreSQL server and returns one bounded raw response;
this module itself has no HTTP, token, subprocess, Azure SDK, restore, marker,
database, or deployment capability. The checked-in policy additionally pins
the Azure management origin and OAuth resource, API version, GET-only method,
no-redirect and one-request contract, timeout, media type, and response-size
limit. A future trusted transport must implement and independently prove those
constraints before the policy can be enabled.

The response verifier hashes the exact raw bytes, rejects duplicate JSON keys,
and requires the exact ARM resource identity, type, normalized location,
PostgreSQL major version, FQDN, `Ready` state, enabled public-access state,
seven-day retention, disabled geo-redundant backup, and `Primary` replication
role. The database name in the receipt is copied from the authenticated base
binding; Azure's server GET does not observe a database. Extra provider fields
are ignored but remain covered by the raw-response digest. The provider's
fractional `earliestRestoreDate` is preserved exactly and must be no later than
the approved whole-second restore point. Receipt expiry is the earlier of the
base-binding expiry and 60 seconds after the post-response trusted clock read.

This evidence supports only the claim `provider_advertised_window_only`.
Azure's server response exposes an earliest restore point, not a
latest-restorable point or proof that a restore will succeed. Recoverability
therefore remains an approved live-rehearsal result. The sealed provider result
is another partial, not a final `RestoreEvidenceBinding`; the later composite
boundary must reverify the cryptographic server receipt, recompute both byte
digests, join the GitHub-attestation result, and recheck freshness immediately
before minting final authorization. The canonical provider policy is disabled,
all transport/workflow/network activation flags are false, and the supported
entry point refuses before invoking a provider.

`github-attestation-authentication-policy.json` and
`scripts/authenticate_staging_github_attestation.py` define the independent
offline provenance boundary. The verifier consumes the exact manifest bytes
retained by the validated one-read snapshot and the same sealed
server-authenticated partial used by provider observation. It requires an
independently delivered, exact-digest-pinned GitHub-only Fulcio/TSA root and a
digest-pinned Linux `gh` 2.97.0 verifier. The exact command uses only the local
bundle and custom root, disables public-good verification, denies self-hosted
runners, and pins repository, workflow identity, OIDC issuer, source ref, and
workflow/source digests.

Successful private-seam verification requires one Sigstore v0.3 bundle, one
verification result, and one `evidence-manifest.json` subject whose SHA-256 is
computed over the retained file bytes rather than canonicalized JSON. The
returned bundle must equal the supplied bundle, and the decoded DSSE payload
must equal the returned statement. The raw leaf certificate is checked against
the CLI certificate summary and exact repository/workflow/run claims; its URI
SAN plus GitHub deployment-environment and raw-token-subject extensions are
parsed directly from DER. Only GitHub's RFC3161 timestamp authority is accepted,
and every verified timestamp must remain inside the authenticated manifest
window. Statement predicates remain workflow-controlled claims, not independent
identity evidence.

The resulting `VerifiedGitHubAttestation` is sealed, short-lived, bound to the
same exact base bytes as the provider receipt, and still cannot cross the final
safe-submit boundary. The checked-in policy remains disabled: its Linux
verifier-binary digest and independently delivered GitHub-only trusted-root
digest are intentionally unconfigured, and no subprocess or network adapter is
implemented in this module. Workflow integration, composite binding, and live
restore activation remain separate reviewed steps.

`restore-evidence-composition-policy.json` defines the closed-world join for
these three sealed proofs. It pins the exact evidence-manifest,
server-authentication, provider-observation, and GitHub-attestation policy
bytes. Both independent receipts must name the SHA-256 of the exact sealed
server-authenticated record; every overlapping manifest, source, candidate,
workflow, and restore-time field must equal that record. The composition also
recomputes the provider and attestation receipt digests and carries the exact
server-authentication receipt digest, so the final binding contains precisely
three upstream receipt digests.

Composition permits one trusted-clock read only after all seals, the exact
canonical server-authentication/provider/attestation receipt bytes, policy
pins, digests, and cross-bindings have been revalidated.
The read cannot precede any upstream trusted observation, authentication, or
verification time. Final expiry is the minimum of the three receipt expiries,
and the clock must remain strictly before it; composition can only shorten an
authorization window. The output field set is exactly the sealed
`RestoreEvidenceBinding` accepted by safe-submit, with no unknown fields.

The policy and every activation flag remain disabled. The contract adds no
workflow call, executor handoff, restore request, provider access, or mutation;
workflow integration, trusted-executor integration, and restore activation are
explicit blockers requiring separate reviewed work.

The protected rehearsal workflow now removes the legacy direct provision path
that accepted caller-supplied digests. Its `provision` job is a one-minute
credentialless tombstone with `permissions: {}`: no checkout, protected
environment, OIDC token, Azure login, receipt upload, controller provision, or
safe-submit call is reachable. It always reports the missing trusted
coordinator and fails. Exact manual cleanup and the gated hourly sweep remain
on the established cleanup identity and controller; there is no failed-run
compensation branch because this provision tombstone cannot create resources.

This is not composition activation. The upstream proofs and final binding are
opaque, module-sealed Python objects rather than bearer JSON. GitHub outputs or
artifacts would serialize only their records; a later step or job cannot
reconstruct the exact sealed object and is rejected by the consumer. A future
trusted coordinator must therefore run manifest validation, server receipt
authentication, provider observation, offline attestation verification,
composition, the last freshness check, and direct safe-submit invocation in
one process. Its review must keep original bytes available for revalidation and
must not add a deserialization minter.

`restore-trusted-executor-policy.json` now closes that future boundary without
activating it. Its only public shape is
`execute_trusted_restore(bundle_dir, hmac_key_path, attestation_bundle_path,
trusted_root_path, ttl_hours)`. The exact six-file directory, HMAC key,
attestation bundle, and independently delivered GitHub-only root must each be
opened once with bounded no-follow reads. The sealed snapshot retains the exact
validated result, evidence manifest, and server-scope receipt bytes; accepted
digests bind the candidate, inventory, quiescence, and checksum inputs without
reopening them. The key, attestation bundle, root, snapshot, and opaque seals
remain in the same process until the direct safe-submit call returns.
Caller-supplied restore, workflow, receipt-digest, or identity fields are not
accepted.

The policy pins all eight upstream, envelope, and safe-submit policy files and
requires one shared non-regressing clock. The validated snapshot and sealed
server binding flow unchanged through one provider GET, one offline verifier
invocation, composition's final freshness read, deterministic request
derivation, and at most one `SafeRestoreSubmitter.submit` invocation. Runtime,
provider, verifier, submitter, private-runner, workflow, and restore activation
remain false; exact job/image/identity/network pins are explicit placeholders.
No workflow or live environment was changed.

Exact activation blockers remain fail-closed: composition has
`activation_ready: false` and all of
`trusted_composite_verifier_implemented`, `workflow_integrated`,
`trusted_executor_integrated`, and `restore_activation_authorized` false;
its blocker codes are `restore_evidence_composition_policy_disabled`,
`upstream_policies_disabled`, `trusted_composite_verifier_unimplemented`,
`workflow_integration_unimplemented`,
`trusted_executor_integration_unimplemented`, and
`restore_activation_unimplemented`. Safe-submit has
`blob_marker_store_implemented`,
`blob_marker_provider_fidelity_proven`, `private_runner_binding_implemented`,
`workflow_integrated`, and `trusted_evidence_binding_implemented` false.
Upstream policies are disabled, the Linux verifier and GitHub-only root digests
are unconfigured, and storage/network/RBAC/WORM/provider-fidelity pins remain
`CONFIGURE_EXACT_...`. No dispatch, live proof, restore, cutover, or Azure
mutation occurred.

The checked-in `server-scope-authentication-policy.json` and server-scope policy
are both disabled, so the authenticator refuses before reading a key or bundle.
Even a successful future authentication result remains blocked
(`activation_state: blocked`): provider observation, GitHub attestation
verification, protected workflow storage/integration, and restore activation
are separate milestones.

Activation still requires a separately reviewed, trusted baseline capture, an
exact primary-database binding to the full inventory, proof that every
non-primary connectable database is relation-free, and confirmation that the
Azure identity can read every required surface. This binding adds no workflow,
credential, database write, restore, deployment, safe-submit, or promotion
path. The PostgreSQL connection cannot independently attest its Azure resource
ID, and the local validator deliberately has no HMAC key with which to verify
`source_resource_hmac` or `evidence_hmac`; authenticated provider binding and
cryptographic attestation remain separate activation prerequisites.
