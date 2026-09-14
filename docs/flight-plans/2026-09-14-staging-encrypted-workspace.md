# Empty encrypted staging workspace (Tier 2)

## One outcome

Prepare and verify one empty local encrypted workspace for the separately
approved bounded staging snapshot route. This is not a replacement recovery
platform. Existing staging resources are reused; no Azure resource is created
or changed. The operator confirmed the clinical QA records are entirely
synthetic; session, workforce and provider material remains sensitive.

The observed laptop has FileVault off. A 0700 directory is not encrypted
storage. The human-only `prepare-empty` operation creates an 8 GiB maximum
APFS filesystem in an AES-256 sparse disk image, inside a new private directory
under `/private/tmp`. It does not enable FileVault, make a database connection,
copy data, open a firewall, execute SQL, run an application or start a container.
The sparse backing file grows as needed; the virtual filesystem size is fixed.

Native `hdiutil -stdinpass` receives the passphrase directly from the regular
Terminal, using its documented echo-off TTY behavior. Python never reads the
passphrase. Use a new strong passphrase, retain it outside this task, and never
send it to the agent or save it in Keychain. There are no passphrases in argv,
files, manifests, logs or tests. The two native create/attach prompts are not
new requests for scope approval. There is no sudo or global preference change.

## Exact identity of the destination

The helper rejects aliases, non-private permissions, unexpected owners and
hardlinked backing/manifest files. Native encrypted-image metadata must bind
one image to exactly one mount/device, with exact capacity, expected owner,
no shadow file and a writable image. Independent disk metadata must confirm
that exact mount/device is APFS, writable and honors ownership. An ordinary
directory left after detach cannot pass. The mount must have a distinct device
from its parent. Spotlight is disabled for creation; a no-index marker is
created before the workspace can hold snapshot material.

The manifest is value-minimized local evidence, not a receipt or permanent
proof of safety. It records creation-source and command hashes, image inode,
mount/device binding and time. Later read-only `verify` requires the caller's
exact protected manifest hash, the same helper source, image inode and live
mount binding. Detach/re-attach or source changes may invalidate it: fail and
prepare a newly reviewed binding, never edit the old evidence to pass.

Local native-utility/runtime hashes, operator identity and the manifest hash
must be pinned in the later independent exact handoff. The manifest is not
Azure identity evidence. Same-user/root compromise, screenshots, VM host memory
and arbitrary external tools are not made safe by this helper. The downstream
capture must recheck the mounted destination immediately before writes and
use an opened directory/file descriptor with exclusive no-follow creation;
never fall back to a plain directory if a mount disappears. No capture code is
included here or authorized by a verification result.

## Ordering and retained gates

1. Independent final source review, required CI and normal protected merge.
2. An independently reviewed operator setup command can create this EMPTY
   workspace before the short live-action window. The helper is not run by an
   agent. No existing source/plan/receipt is reused or extended by this step.
3. Prepare the actual snapshot plan using its real timestamp, exact source,
   encrypted destination, one action-time IPv4/rule, current configured-user
   identity bound to the exact ARM token, PostgreSQL client and TLS CA hashes,
   least-privilege assessment, source revision and data/integrity contract.
4. Independent exact plan review, then actual Terminal-presence confirmation
   before a fresh ten-minute handoff and independent exact handoff review.
   Four-hour plan and ten-minute receipt limits remain. Expired or changed
   source/plan/identity/destination requires fresh preparation and review.
5. Human-only live capture, at most 30-minute single-IP staging firewall rule,
   exact new-rule cleanup and absence verification. None is implemented here.
6. Isolated local restore, reviewed treatment of credentials/session/provider
   fields, exact four-migration rehearsal and aggregate/digest integrity evidence.
   No application starts with copied credentials. No default Docker volume on
   unencrypted storage. Staging deployment and Auth/Zoom acceptance are later.

## Verification and recovery

New secretless tests exercise metadata and command construction, fail-closed
path/owner/encryption/capacity/mount checks, native invocation boundaries and
manifest binding. Native commands are mocked: tests never create a disk image,
receive a password, connect to Azure or copy data. Actual native preparation
must succeed in the human's Terminal before this destination is accepted.
Do not describe unit tests as native encryption or restore/rehearsal evidence.

Failure preserves the printed task-specific path and all existing artifacts.
No automatic detach, deletion, key erasure, overwrite, cleanup of another image,
or retry is performed. An incomplete empty workspace is not valid evidence.
After a failure, inspect only that exact path/mount; prepare a separately scoped
recovery command if needed. No database data can be lost by this helper because
it has no database/copy operations. A future data-bearing workspace needs exact
retention/deletion approval; do not apply empty-workspace assumptions to it.

This source change has no automatic activation, application migration, flags or
deployment. Recovery is to stop using the helper; retain already-created images
until exact ownership and contents are known. No Azure resources or persistent
staging records are deleted as rollback.
