# Exclusive public snapshot packet publication

Tier 2: evidence integrity at the human preparation/capture boundary. DORMANT
until independent final-head review. No deployment, execution, review-transmission
or merge permission is created by this PR. No new service, runtime or dependency.

## Outcome and exact boundary

Publish the existing six-file read-only preparation packet or sixteen-file
already-reviewed capture packet without inventing a filesystem receipt/claim or
rewriting the reviewed bytes. This stays OUTSIDE the existing 18-file human
runtime. This helper changes no runtime18 bytes; current exact runtime pins must
come from their separate independent whole-runtime review, not this paragraph.

Review48 found a pending-alarm teardown race. Closing now makes the handler
record-only, masks signals, disarms/drains its pending one-shot, restores the
prior handler/mask through nested finally, then checks the original30second
deadline. A caller-owned pending alarm refuses before timer/handler changes.
Regressions include a real pending alarm with SIG_IGN and sentinel prior handlers,
no prior invocation or returned publication, full state restoration, and expiry
during final handler restoration. Original48FAIL remains until exact re-review.
Three new and five affected methods PASS0.021s with invented public packets and
actual local alarm/ACL/file operations. Ruff/format/Pyright/diff PASS; no live I/O.

The two public APIs accept the typed original source/review/plan evidence and
externally anchored preparation or handoff/review hashes. These inputs and the
helper itself must first be authenticated by the actual reviewed caller. A local
hash, dataclass or supplied stat tuple is not self-authentication. Internal file
helpers are trusted implementation details, not capabilities for untrusted code.
This public-file helper is not a sandbox against malicious same-UID/root code.

`publish_preparation` reconstructs the exact six files at their ORIGINAL creation
time and compares every byte. The public builder now factors that content check
from its new-content function, which still always uses actual current time.
No expiry or presence timestamp is renewed. Fresh source <=10 minutes, original
preparation expiry, current operator, complete exact source review, workspace and
tool bindings remain required. The exact human-command review is still owed.

`publish_capture` uses the existing packet_files validator, requiring the real
independent exact plan AND handoff reviews, actual identity/token lifetime,
unchanged plan/source and remaining handoff lifetime. It cannot create a new
plan, handoff, source observation, review or receipt. Both APIs require actual
same-task Terminal presence within five minutes of publication and at the end.
The timestamp cannot independently prove who confirmed presence; the actual
caller must obtain it from the user, never a return schedule.

## Native file behavior

- Only the fixed task-private parent and closed human-preparation/human-snapshot
  namespaces; fixed names/counts/per-file sizes, <=12MiB total. No generic path,
  recursive file operation, credential read, network request or child process.
- Retain no-follow descriptor traversal of every ancestor. Root-owned sticky
  ancestors are allowed; leaf parent must be current-user-owned0700 without ACL
  entries. Deny-only ACLs are allowed only on ancestors. macOS uses read-only
  descriptor ACL inspection from the absolute system library; Linux rejects
  extended POSIX/default/NFS4 ACLs. No permission repair is performed.
- Exclusively create the0700 packet directory and each0400 regular single-link
  file. The existing caller's umask is never changed. No exist_ok, overwrite,
  rename-to-replace, symlink following, implicit cleanup or retry.
- Preparation also exclusively creates the distinct pinned empty0700 observation
  directory expected by the human preparer. Its descriptor/identity/emptiness are
  retained until return; no observation file is created here. Its native identity
  is returned for later read-only revalidation, not permanent mount readiness.
- Write bounded chunks, fsync, verify exact bytes/hash/type/owner/mode/link count
  and inode/timestamps. Write the existing entry artifact LAST (preparation.json
  or handoff.json), never an extra success marker. Revalidate all files, directory
  identities, ancestor links, presence and input expiry before returning.
- One30second main-thread alarm for the entire operation; refuse an existing or
  blocked alarm. No nested/reset deadline. Short signal masking only for owned
  descriptor acquisition/close and handler changes; writes remain interruptible.
  A swallowed alarm still fails. Wall/monotonic divergence also refuses.
- Source/time changes, collisions, partial writes, fsync, close or final checks
  failing yield one content-free exception and NO returned success. Preserve all
  partial or complete-but-unaccepted files; never erase evidence to enable retry.
  An on-disk file after failure is not proof publication/acceptance succeeded.

The frozen Publication result contains paths, hashes and native identities only.
It is not a claim, receipt, source provenance, plan approval or execution grant.
Passing it as `revalidate` selects READ-ONLY verification of the same original
inputs/identities; no mkdir/write/fsync/renewal. The file policy is independently
enforced even when a caller supplies a matching stat tuple for an unsafe file.
Presence and time gates still apply; it cannot restore an expired publication.

## Integration, remaining authority and recovery

Actual caller sequence: authenticate reviewed source/helper bytes and all
original evidence, obtain actual Terminal confirmation, build the six public
preparation files, publish them, independently review the exact human command,
read-only revalidate, then let the human run preparation. Use its actual fresh
identity observation to create/review the plan and handoff. Publish the already
reviewed sixteen capture files and revalidate immediately before the reviewed
human command. The human consumer retains its own native/source/artifact and
foreground checks. No command is generated or executed by this PR.

The returned identities are transient observations, not protection against a
malicious operator replacing files later. Do not hand out a command based on a
failed/changed/expired publication. Preserve the failed directories, diagnose,
and prepare separately reviewed fresh evidence when appropriate. Never silently
overwrite/reuse or delete them. No Azure/database rollback is needed: this helper
changes only newly created public artifact files in its exact task namespace.

## Verification actually performed

22distinct new invented-fixture methods PASS, plus two affected existing
preparation producer/reader tests. Native local publication and native reader
compatibility are real, but source/review/identity/presence are wholly invented
2030fixtures and process/approval authority is mocked. No human main/consumer,
account/cache, service, encrypted image or live data was used. Existing fixture
definitions were imported only; their unchanged suites were not replayed.

Coverage includes both catalogs, exact permissions/bytes, read-only revalidation,
unsafe names/anchors, expiry/changed plans, existing paths, symlink/group-write
parents, file replacement/link count, observation-directory replacement, partial
write/fsync/close failure, total timeout, swallowed alarm and ACL semantics.
An initial reader-test clock subclass mismatch was corrected in that fixture.
A new regression proved that an invented supplied stat tuple could otherwise
bless a writable file during revalidation; independent file-policy checks fixed
it, and the writable/hardlink regression plus four affected checks pass.

Ruff/format/Pyright/whitespace pass. No paid CI, Azure review transmission,
source merge, real packet or expiry-bearing artifact was created. Existing
18-file runtime native startup and earlier passing suites were not rerun because
those exact bytes did not change. This is preparation progress, not a live
snapshot, restore/migration, staging release, Clerk or Zoom acceptance result.
