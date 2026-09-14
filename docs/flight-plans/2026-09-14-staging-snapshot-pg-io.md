# Fixed-target staging snapshot I/O (Tier 2)

## Outcome and scope

One dormant PostgreSQL16 capture component for the already-approved bounded
snapshot route. Reuse installed PostgreSQL16.15 clients and existing staging.
No new service, role, grant, network, recovery platform or application change.
Direct module execution refuses; no CLI or credential discovery exists here.

The future complete human launcher supplies authenticated credentials, a
verified encrypted payload descriptor, exact runtime/native-binding callbacks
and one original capture deadline. Those inputs are not authorization or proof.
The assistant does not execute these live operations. Tests use invented bytes,
mock clients/native encryption and short-lived credentialless Python children.

## Exact read-only source and output

Only cosmaneura-staging-postgres.postgres.database.azure.com:5432 database
staging, canonical source revision89c31f0ae275, PostgreSQL16. The source DSN must
have only the accepted sslmode parameter; arbitrary host/database/port/options
and control characters refuse. Provenance is still the launcher's job: parsing
a string cannot prove it came from the bound application.

The child environment is constructed from an allowlist. No ambient PG/DYLD/
proxy/service configuration, password files or application/provider settings.
Credentials remain in memory/child environment, not argv, files or diagnostics.
An existing default PostgreSQL TLS-material directory is refused, not modified.
verify-full uses the explicit pinned CA; readonly startup options, connect/lock/
statement timeouts and fixed SQL are mandatory. The existing role is broader
than read-only (owner, CREATEDB/CREATEROLE/BYPASSRLS); the exact plan must assess
the constrained command honestly. No new grant or least-privilege claim.

Read-only source observations before/after require the exact DB, PG major,
TLS, transaction-readonly and sole revision. pg_dump writes one custom archive
through a held descriptor, exclusive/no-follow, at most1GiB. Verify payload
device/inode/owner/mode and archive name/inode/device/owner/single link/size.
No source writes, app import/start, migration or restoration occurs.

Native pg_restore fully decodes the archive to /dev/null without a database
connection or credentials, then extracts the archived Alembic row into a bounded
memory pipe. This is archive validation, NOT an isolated restoration rehearsal.
Final native encrypted-binding checks and descriptor rehash reject replacement
or same-size content changes. Protect the completed archive0400. Retain partial
encrypted files for exact inspection; no overwrite, automatic deletion or retry.

## Time and child process handling

One supplied absolute monotonic deadline, maximum20minutes, covers the entire
capture including filesystem writes/fsync and native/runtime callbacks. A
foreground SIGALRM guard refuses an active timer, blocked alarm or non-main
thread; it never replaces active timing authority. It restores the previous
handler/disarms on all exits and refuses even a swallowed alarm. The future
launcher must finish/disarm its confirmation timer before capture, without
extending the original plan/handoff or access-window deadline.

Stream output in bounded chunks, with no shell and discarded native diagnostic
stderr. Each child has its own session/process group. Non-reaping wait keeps the
leader PID allocated until group cancellation. Native exact process-group
readback treats denial/unknown state as failure, not absence. A zombie-only
group can be reaped without signalling; active/unknown groups get at most one
SIGKILL attempt before reaping. No later cleanup signals a potentially reaped
PID. Interrupt/denied inspection still attempts owned-child cleanup; uncertain
wait state cannot become success.

Ctrl-C/alarm delivery is deferred only across trusted spawn/handle registration
and short cleanup, to avoid losing child ownership between a native return and
Python assignment/reaping. Cleanup has separate bounded process inspections
(2seconds each) and wait(5seconds). This does not guarantee progress on a hung
kernel, failed hardware, host crash, escaped malicious descendant, compromised
runtime or other same-user process. It is a fixed reviewed-client boundary,
not a general sandbox for untrusted executables. Any unresolved child or timeout
refuses acceptance and requires the launcher's human-removal fallback.

## Integration and retained gates

The complete reviewed launcher must bind normally merged source and every
runtime/client/library/CA dependency, configured user and exact ARM-token/MSAL
home-account identity, fresh independent exact plan/handoff reviews, actual
regular-Terminal presence and genuine foreground confirmations. No stub/lambda
runtime or encrypted-binding callback is valid for live use. Native verification
must bind the original PR394 image, mount, manifest and held directory inode;
the ordinary empty directory left by a detach must not pass.

PR396's separately approved HUMAN-managed single-current-IP30minute window
owns the sequencing. This module has no firewall calls or Azure client. The
human creates/removes the rule, attests all mutations terminal/removal completed,
and a read-only adapter checks absence/baseline. No atomic ownership, automatic
TTL or crash-proof closure claim. Capture failure does not authorize opening a
new window, changing a plan, renewing a receipt or retrying a live operation.

Source PASS and CI do not approve the missing live integration or execution.
Exact4h plan timestamp and10min handoff expiry remain; no claims or gate bypass.
After actual capture, independently scoped isolated restore, credential/session/
provider treatment and exact four-migration rehearsal must pass before release.
No copied credentials may activate an app/provider. No live migration,
deployment, resource cleanup or additional merge authority is inferred.

## Validation and recovery

The original draft had27distinct new methods PASS. This source adds9new
deadline/interrupt methods;22new or affected methods pass (9new,13affected).
Fourteen unchanged passing methods were not replayed. Ordinary temporary
directories hold ONLY invented bytes; no actual PG/Azure/encryption operation.
Relocated test import smoke, Ruff0.11.6 format/lint, Pyright1.1.399/Linux and
whitespace checks pass. Required final-head CI and independent source review
remain gates, not replaced by these statements.

Recovery for this dormant source is to stop using it; no deployed state changed.
If future live capture fails, preserve its protected partial artifact and remove
only the human-managed exact temporary rule under the reviewed fallback. Do not
delete existing snapshots, images, resources or QA data as rollback.
