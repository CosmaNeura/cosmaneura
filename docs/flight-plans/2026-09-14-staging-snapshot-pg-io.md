# Fixed-target staging snapshot I/O (Tier 2)

## Outcome and scope

Dormant PostgreSQL16 capture reuses installed16.15 clients/staging; no new resources.
No CLI/discovery: direct execution refuses. Only the complete human launcher
supplies authenticated credentials, encrypted descriptor, exact runtime callbacks
and deadline. Tests use invented bytes/mocks/credentialless children, not authority.

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

Credentialless native pg_restore decodes to /dev/null and extracts the Alembic row
into bounded memory: archive validation, NOT restore rehearsal. Native encrypted
binding/descriptor rehash reject replacement or same-size changes. Completed
archive0400; retain partial encrypted files without overwrite, deletion or retry.

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
Historical FAIL/results remain distinct in the exact review ledger.

Review44–46 corrections: every pre-reap read must have unique PIDs including the
unreaped leader. Empty/duplicate/missing-leader/denied evidence permanently fails
acceptance, retains the child/pipe and refuses before wait. Cancel the owned group
at most once unless it is exactly a lone zombie; never signal a reaped PID.
Only proven lone-zombie state can reach wait, followed by an empty post-reap read.
One stored five-second deadline covers ALL cleanup calls for that child. Retry
may only inspect/reap in its original remaining time, never signal or gain a
positive timeout floor. Every native read, including the final empty read, has
a post-call absolute-deadline check. Later recovery cannot erase earlier failure.
Original44–46 FAIL reviews remain; fresh exact independent review is required.

Output is streamed in bounded chunks, without shell or native stderr. A private
session/group plus non-reaping wait retains leader ownership until retirement.
Interrupted/denied inspection still attempts owned-child cancellation; it does
not turn uncertain cleanup into success or release an unproved child handle.

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

PR396's approved HUMAN-managed single-current-IP30minute window owns sequencing.
No firewall/Azure calls here: human creates/removes, attests mutations terminal
and removal complete; adapter verifies absence/baseline. No atomic ownership,
TTL/crash-proof claim or failure-authorized new window/plan/receipt/live retry.

Source/check PASS is not execution authority. Exact4h plan/10min handoff gates
remain. After capture, isolated restore, credential/session/provider treatment
and actual four-migration rehearsal must pass. No copied credentials may activate
an app/provider; no live migration, deployment, cleanup or merge is authorized.

## Validation and recovery

### September 15 macOS archive-reader correction

Both credentialless `pg_restore` calls now select `--format=custom`, matching
the format already required from `pg_dump`. On macOS, reopening `/dev/fd/N`
shares the descriptor offset: automatic format detection consumes the header,
then the custom reader reopens at the advanced offset and rejects valid bytes.
Explicit format selection avoids that extra read while retaining the held
descriptor, bounded output, full decode, revision check, and original deadlines.
The bootstrap dependency pin and human-command loader/bundle pins follow the
changed source. This changes no firewall, credential, capture, or release scope.

A native regression uses an invented 85-byte empty custom archive and exercises
both validation commands: autodetection fails on macOS, explicit custom format
succeeds. The composition test also requires this flag for both readers.
The retained staging archive passed separate offline decode and archived-revision
checks with explicit format; those observations do not retroactively turn the
failed capture into an accepted run or authorize restore, migration, or release.
Preserve that archive and the recorded failure; do not repeat the Azure/data-copy
steps solely to correct local validation. Subsequent use needs its own exact
artifact validation and the existing isolated-rehearsal decision.

Prior45 correction:17affected methods passed (16in0.200s, signals0.100s); initial
sandbox ps denial ran zero tests. Review46's3affected methods PASS0.019s include
final-read before/at/after expiry. Historical results are not current acceptance.
No unchanged suite, paid CI, credential, PG/Azure or human-consumer execution.

Recovery: stop using this dormant source; no deployed state changed. Live failure
preserves protected partial artifacts; human removes only the exact temporary
rule under reviewed fallback. Never delete snapshots/images/resources/QA data.
