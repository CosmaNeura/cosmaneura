# Fixed-target staging snapshot I/O (Tier 2)

## Outcome and scope

Dormant fixed PostgreSQL16 capture for the approved bounded snapshot route,
reusing installed16.15 clients and staging. No new resources or application change.
No CLI or credential discovery: direct execution refuses. Only the complete human
launcher supplies authenticated credentials, verified encrypted descriptor, exact
runtime/native callbacks and original deadline. Inputs are not authorization.
Agent tests use invented bytes, mock clients and credentialless Python children.

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

The HUMAN bootstrap must lower BOTH RLIMIT_CORE limits to zero before any
identity/cache/token/DSN read, credential-reader construction or sealing.
Sealing, credential parsing, environment creation, capture and pre-spawn recheck
(0,0). The library refuses without altering limits or reading secrets. Review34
established this pre-secret ordering; a later successful spawn cannot prove it.

Review33 established fresh dedicated `python -I -S`: load/attest the exact
source/stdlib/native closure, then `seal_capture_process` ONCE before identity,
network or capture. Missing seal refuses before timer/spawn; shared/threaded or
embedded hosts are unsupported. No worker fallback or expiry refresh is allowed.
Sealing verifies main/Python threads and native count (macOS ps/Linux procfs).
A probed process-lifetime PID-bound audit hook refuses worker/subinterpreter/fork,
ctypes-loading and new imports. It cannot cross a fork. The trusted closure must
not create native workers. This convention is NOT a hostile-code sandbox; final
launcher review must establish fresh exec and complete closure, not infer them.

One supplied absolute monotonic deadline, maximum20minutes, covers the entire
capture including filesystem writes/fsync and native/runtime callbacks. A
foreground SIGALRM guard refuses an active timer, blocked alarm or non-main
thread; it never replaces active timing authority. It restores the previous
handler/disarms on all exits and refuses even a swallowed alarm. The future
launcher must finish/disarm its confirmation timer before capture, without
extending the original plan/handoff or access-window deadline.

Review32 teardown correction: make handler record-only, mask SIGALRM, disarm and
drain its pending one-shot, restore previous handler/mask in nested finally, THEN
propagate expiry. An already-pending caller alarm refuses before installation.
Eight injected teardown steps and a real pending-signal fixture passed; historical
review FAIL remains separate from later correction evidence and local tests.

Review44 runtime correction (September15): an empty/duplicate/missing-leader
pre-reap process list is never evidence of retirement. Every accepted pre-reap
inspection must include the still-unreaped leader with unique PIDs. Initial or
later ambiguity permanently fails cleanup, even if a later observation is valid.
Unless exactly one zombie leader is proven, cancellation is attempted at most
once before reaping. Successful cleanup requires that exact lone-zombie state,
then reaping, then an empty post-reap group. An already-reaped child cannot be
signalled. The complete final bundle and its new pins require independent review;
review44's FAIL is preserved, not converted into acceptance by local tests.

Review45 found that later ambiguity still fell through to wait, and retries
reset the cleanup deadline. Corrected control flow retains the unreaped handle
and pipe on ambiguous evidence; only a proven lone zombie can reach wait. The
first cleanup stores one five-second deadline for that registered child. Retry
can inspect/reap only within its original remaining time and cannot signal again.
No positive timeout floor extends the deadline. Any prior ambiguity permanently
refuses capture acceptance even if later cleanup succeeds. The exact changed
runtime and downstream pins need fresh independent review; review45 remains FAIL.

Stream output in bounded chunks, with no shell and discarded native diagnostic
stderr. Each child has its own session/process group. Non-reaping wait keeps the
leader PID allocated until group cancellation. Native exact process-group
readback treats denial/unknown state as failure, not absence. Exactly one zombie
leader can be reaped without signalling; other/unknown groups get at most one
SIGKILL attempt before reaping. No later cleanup signals a potentially reaped
PID. Interrupt/denied inspection still attempts owned-child cleanup; uncertain
wait state cannot become success.

Ctrl-C/alarm delivery is deferred only across trusted spawn/handle registration
and short cleanup, to avoid losing child ownership between a native return and
Python assignment/reaping. Inspections (at most2seconds each), zombie-retirement
wait and reaping share a five-second cleanup deadline; accepted capture evidence
still has its original capture deadline. This does not guarantee progress on a hung
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

Review45 correction:16affected cases PASS0.200s (initial sandbox ps denial ran
zero tests). Coverage includes ambiguous ownership/retained handle, same-deadline
retry, sub-10ms wait without floor, expired retry without read/reap, post-reap
survivors and actual credentialless-child streaming/timeout/limit/nonzero exit.
Historical counts/failures remain in commits and ledger. No unchanged suite or
paid CI replay, credentials, PG/Azure call or human-consumer execution occurred.

Recovery for this dormant source is to stop using it; no deployed state changed.
If future live capture fails, preserve its protected partial artifact and remove
only the human-managed exact temporary rule under the reviewed fallback. Do not
delete existing snapshots, images, resources or QA data as rollback.
