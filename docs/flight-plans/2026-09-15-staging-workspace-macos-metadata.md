# macOS encrypted-workspace compatibility and bounded empty completion

Risk: **Tier 2**, security-boundary validation. One outcome: make the existing
human-owned empty-workspace prerequisite compatible with the observed native
macOS metadata, preserving refusal and independent-review gates. No schema,
application, infrastructure, credentials, deployment or workflow change.

## Observed failure and scope

The human ran the normally merged, independently reviewed empty setup command.
Creation and attachment occurred, but setup refused with `disk_readonly` before
permission normalization, no-index marker, payload directory or manifest.
The failed workspace remains attached and empty; it must not be retried,
detached, deleted, replaced or chmod'ed by an agent.

Read-only native evidence showed two compatibility defects:

- The pinned `diskutil info -plist` emits `Writable`, `WritableMedia` and
  `WritableVolume`, each boolean true. It does not emit `ReadOnlyVolume`.
  Requiring the absent legacy field to equal false always refused this volume.
- `hdiutil isencrypted` cannot reopen this attached backing file (exit 1,
  `Resource temporarily unavailable`). The native `hdiutil info -plist` record
  already binds encryption true to the exact image, owner, capacity, mount and
  device; mounted verification must use that record. Pre-attach `isencrypted`
  remains in the original new-image creation path.

Native disk metadata SHA256:
`5a8b83611a1f2a7bb6068bb2c93ede0989ebec2bfb5a4035ab28fa17b275aa33`.
Native image-info SHA256:
`04a9e26eb44e307d2ec18f969a5ff8c7a22d86e639de74f72597977f335c480e`.
The corrected pure parsers accepted those actual native responses (local tool
result `d769d0`). Only read-only native information commands and parser functions
were invoked, not a human setup/completion/verification consumer. No write to
the existing workspace occurred. Initial sandbox attempts could not access
DiskManagement/GitHub; supported scoped read-only escalation succeeded.

## Controls retained and completion design

Require all three positive writable fields to be literally true. Reject any
present contradictory/malformed legacy read-only field. Keep exact APFS,
ownership, capacity, no-shadow, unique image/mount/device and native filesystem
identity checks. Missing metadata is not proof of safety. The snapshot session's
bounded native transport now permits only the required `images` and `disk`
queries; the unavailable backing-file query is not silently ignored.
The bootstrap's two exact dependency hashes are updated atomically with those
files. Its existing source-closure test must pass; no stale loader pin is waived.
The public source publisher/command renderer also pins the resulting NEW loader
and full18-file bundle. Historical independent reviews cannot authorize those
new bytes; source publication still requires an actual matching new review.

New `complete-empty` is HUMAN-ONLY and is NOT an automatic retry. Before any
workspace modification it requires all three real TTYs, exact source hash,
explicit exact workspace/device/root/image/mount identities, private owned root
and image, a still-attached distinct encrypted APFS filesystem, and an empty
0755 mount. The root must contain exactly the image and mount, no manifest or
other artifact. Duplicate/unknown/missing/ill-typed binding fields refuse.

It observes native binding twice and holds the mount directory descriptor.
Permission normalization uses `fchmod` only on that held verified descriptor,
never a pathname that could fall through to plaintext after detach. Initialization
rechecks exact image/mount binding and emptiness before descriptor-relative
exclusive marker/payload creation. Existing content is neither adopted nor
overwritten. An exclusive 0400 manifest records `setup_operation` and whether
this process created the image. `creation_command_sha256` still describes the
requested original creation command; completion does not claim to have rerun it.
The public-plan parser accepts either the existing legacy schema or both new
operation fields with strict consistent boolean values, no partial/extra fields.

No create, attach, detach, delete, credential prompt, cloud/database/container
operation, cleanup, retry, plan, receipt or capture authority is added by
completion. Trusted OS/same-user integrity remains the existing threat model;
these checks are not a hostile-same-user or dynamic-library attestation claim.

## Validation (affected scopes only; no hosted CI)

- Five focused metadata/binding cases passed (`342ff4`, 0.001s).
- Seven affected mocked `WorkspaceTests` passed (`883a24`, 0.030s). The first
  invocation failed during sandboxed own-process discovery, before any test;
  supported scoped escalation was used, with native disk operations mocked.
- Six new completion/binding refusal and temporary-fixture cases passed
  (`67fc0c`, 0.004s). No real image was created or mounted. Initial extracted
  publisher lint failure and five type errors were corrected, not hidden.
- One new empty-held-mount race/refusal case and three affected/new public-plan
  manifest cases passed (`da72d6`, 0.001s + 0.003s).
- Ruff and Pyright on all six affected Python files pass (`da72d6`); whitespace
  and final change-budget validation must be recorded against the pushed head.
- A final integration inspection found the bootstrap's two affected source pins
  also required updating. The prior b5982c3a review request and evidence remain
  historical, not final-head coverage. Run the one affected exact dependency-pin
  test and bootstrap static checks; do not replay the 22 passing cases above.
- The final publisher/renderer closure inspection additionally updates its two
  loader/bundle constants. Its existing real-source (but invented provider/review)
  ProductionPinTests case is the affected validation; never publish or execute a
  real human packet to test this. Earlier head evidence is retained as history.

Previously passing unaffected tests are reused, not rerun. Frontend/application
tests do not apply to this isolated helper/parser change. No GitHub Actions,
Azure review, build, network mutation or live data access was performed by tests.
Native parser compatibility is evidence, not successful human completion.

## Release and recovery gates

1. Independent Cursor review of the final substantive PR head and integrations;
   durable exact-head findings/dispositions and genuine scoped local checks.
2. Named user approval and normal protected merge, preserving Actions OFF and
   all repository protections. Earlier #417/#418 merge approval is fulfilled
   and does not authorize this new PR.
3. Prepare a NEW protected source capsule and independently reviewed exact human
   completion command, pinning the observed partial workspace and native/runtime
   identities. Do not overwrite the old capsule or present the failed command.
   Obtain explicit bounded human completion scope before operator execution.
4. Human runs that exact command in regular Terminal. Present it directly as a
   copyable block, one step at a time. On refusal preserve state and diagnose;
   no automatic repeat/cleanup or success inference from a shell exit alone.
5. Actual snapshot still needs fresh source composition review, explicit account/
   ARM-token identity evidence, current plan, independent exact plan/handoff
   review and the approved human-managed firewall/snapshot controls. Five
   source modules change here, so old review58's bundle is NOT a review of the
   new bundle. Cursor PR review cannot be relabeled as an Azure ReviewRecord.

No ten-minute capture clock starts during empty setup/completion. Never retime or
reuse expired provider observations, plans or handoffs. The separate snapshot,
isolated ONLINE four-migration rehearsal, build/release and UI/Auth/Zoom acceptance
remain incomplete. Recovery is to stop and preserve exact state; a reviewed
source revert does not undo native state and authorizes no cleanup.
