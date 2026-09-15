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

Review34 found that the original pre-spawn core-limit check was too late for
parent-held credentials. The HUMAN bootstrap must irreversibly lower BOTH
RLIMIT_CORE soft/hard limits to zero before any identity/cache/token/DSN read,
before constructing credential readers, and before sealing. Sealing now requires
(0,0); credential parsing, environment creation and capture entry recheck it.
The pre-spawn check remains. The library refuses; it never silently changes
limits or retrieves secrets to establish this invariant. A pre-secret bootstrap
failure must exit without any credential retrieval. The final launcher review
must verify this ordering, not infer it from a successful later spawn check.

Review33 identified that per-thread signal masking is unsafe with any additional
unblocked worker. The corrected interface requires the complete HUMAN launcher
to be a fresh dedicated `python -I -S` process. After loading/attesting its pinned
source and standard-library/native closure, it must call `seal_capture_process`
ONCE before identity/network/capture operations. Shared hosts, embedded launchers
and threaded callers are unsupported; no adapter fallback silently creates a
worker or refreshes an expired action. Missing seal refuses before timer/spawn.

Sealing verifies main-thread/Python thread count and actual OS thread count
(macOS fixed read-only ps; Linux /proc/self/task), then retains a process-lifetime
audit hook that refuses Python worker creation, subinterpreters, fork, ctypes
loading and imports of not-yet-loaded modules. The hook is probed before use;
its PID binding cannot carry across a fork. The trusted pinned closure must not
create native workers internally. This is an enforced calling convention for
that reviewed closure, NOT a sandbox for hostile code already loaded. The final
launcher review must establish fresh exec and completeness of that closure;
this library still has no runnable live launcher or authority of its own.

One supplied absolute monotonic deadline, maximum20minutes, covers the entire
capture including filesystem writes/fsync and native/runtime callbacks. A
foreground SIGALRM guard refuses an active timer, blocked alarm or non-main
thread; it never replaces active timing authority. It restores the previous
handler/disarms on all exits and refuses even a swallowed alarm. The future
launcher must finish/disarm its confirmation timer before capture, without
extending the original plan/handoff or access-window deadline.

Review32 found a P1 alarm-during-teardown race. Corrected teardown first makes
the capture handler record-only, masks SIGALRM, disarms and drains a pending
one-shot signal, then restores the caller's handler and original mask through
nested finally blocks. Only after that restoration does it propagate recorded
expiry. A caller-owned already-pending alarm refuses before handler installation.
Deterministic tests inject expiry at eight teardown steps; another test delivers
a real pending signal in the test process and proves it never reaches the
restored prior handler. The original review FAIL remains until independent
re-review of this correction; local tests are not independent acceptance.

Review44 runtime correction (September15): an empty/duplicate/missing-leader
pre-reap process list is never evidence of retirement. Every accepted pre-reap
inspection must include the still-unreaped leader with unique PIDs. Initial or
later ambiguity permanently fails cleanup, even if a later observation is valid.
Unless exactly one zombie leader is proven, cancellation is attempted at most
once before reaping. Successful cleanup requires that exact lone-zombie state,
then reaping, then an empty post-reap group. An already-reaped child cannot be
signalled. The complete final bundle and its new pins require independent review;
review44's FAIL is preserved, not converted into acceptance by these tests.
Thirteen new/affected secretless fixture and real dedicated-child tests passed
in0.197s. They cover ambiguous reads with/without survivors, denied/later reads,
post-reap survivors, repeated cleanup, lone zombies, actual output/timeout/nonzero
exit and descendant absence. No PostgreSQL connection, credential or human
consumer ran. Ruff/format, explicit-interpreter Pyright and diff checks pass.

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

The original draft had27distinct new methods PASS. This source adds9new
deadline/interrupt methods;22new or affected methods pass (9new,13affected).
Fourteen unchanged passing methods were not replayed. Ordinary temporary
directories hold ONLY invented bytes; no actual PG/Azure/encryption operation.
Relocated test import smoke, Ruff0.11.6 format/lint, Pyright1.1.399/Linux and
whitespace checks pass. Required final-head CI and independent source review
remain gates, not replaced by these statements.

Review32 correction adds3new methods. Two new teardown methods plus6affected
alarm methods passed; the new entry-pending-alarm method also passed separately.
There are now39methods, with25distinct new/affected methods checked during this
revision series and14unchanged passing methods left alone. No live client or
encryption operation was run. Final source still needs independent re-review.

Review33 correction adds6new methods PASS: real unblocked Python and unregistered
native threads refuse before entry; post-seal worker/native-loader/fork attempts
refuse; actual SIGINT and SIGALRM between Popen return and handle assignment do
not lose/release an unreaped child; real pending alarm plus attempted worker
creation at teardown cannot reach the prior handler. Fourteen affected prior
methods PASS; two additional selected cleanup cases initially had incorrect
test-class selectors (loader errors, not executions) and are checked separately.
Signal/child/payload tests now execute in their own fresh sealed processes when
called by shared pytest, never freezing its runner. No unchanged unrelated
contract tests are replayed. Local success still is not independent acceptance.

Review34 correction:1new plus6directly affected methods PASS. Nonzero core-limit
fixture refuses before seal/native inspection, DSN parsing, environment lookup
and capture entry. Dedicated test bootstraps lower only their own core limits
before running invented fixture code; they never enable core dumps. Existing
thread/signal and pre-spawn checks remain covered.46methods now; no live operation.

Recovery for this dormant source is to stop using it; no deployed state changed.
If future live capture fails, preserve its protected partial artifact and remove
only the human-managed exact temporary rule under the reviewed fallback. Do not
delete existing snapshots, images, resources or QA data as rollback.
