# Exact empty encrypted-workspace setup command (draft)

Tier 2. REVIEW DRAFT ONLY. Do not run until independent exact command review is
recorded, current merged source/runtime readback succeeds, and the human confirms
a regular Terminal is open. This is the existing approved local snapshot setup,
not new deployment authority, a snapshot handoff or an expiring receipt.

## One bounded outcome

Prepare ONE empty 8GiB-maximum AES-256 APFS sparse image on the existing Mac,
using the already merged PR394 helper. No Azure, database, application, image
build, network, CI, registry or paid runner. No new cloud/external service cost.
The helper creates/attaches only its new uniquely named disk image; it neither
enables FileVault nor deletes/detaches an existing image.

The human supplies a NEW strong disk-image passphrase to native hdiutil in
Terminal, then supplies that same passphrase for attachment. Never send it to
an agent/chat, put it in argv/logs/files, or store it in Keychain. These are native
password prompts, not repeated scope approvals. The agent never invokes this
human-only operation, supplies the passphrase, or attaches to the user's Terminal.

## Exact command artifact and source

The adjacent staging-empty-workspace-20260915.command contains the exact command
text for review. It is deliberately mode0644 and never automatically invoked.
It is one /usr/bin/env → /bin/sh -c → /usr/bin/env → isolated Python command;
the shell program is an ARGUMENT, not stdin. It must not be piped to sh, sourced
into an existing shell, given invented TTY descriptors or run by an agent.

Merged master at preparation: b4d6e6d9ae47819b2d08af7edc1884cbd1b81c30.
Reviewed source: PR394 final source and PR415 whole snapshot composition review58.
The helper SHA is eaa2d745dcb3bad28452d5db3854e96b6a3c9f37da614c7201d5e6faca267960.
It remains byte-identical to master and the original independent source review.

Public source-only publication succeeded on September15 at14:50:58UTC:
human-source-6c69bb96c17d150daf8010bf18ee1add, under the fixed task-private staging
packet parent. All18source files are owned single-link0400, directory0700, native
ACL/no-follow publication and readback passed. The collector verified54 bounded
Git/GitHub observations, protected master/normal merge/current required checks,
ActionsOFF and all18exact source blobs. Source bundle:
0c79667e6e7088c926b609afb6991ff093098ea725bcd8f7842bfc1760711d50.

This command uses only staging_encrypted_workspace.py from that directory.
No snapshot bootstrap/main, credential resolver or other human consumer runs.
The retained source-only directory has no human plan/handoff/receipt. Refresh
read-only provider/source evidence before presentation; do not regenerate or
overwrite the source directory or any existing runtime to force a pass.

## Pre-execution gates and trust limits

The command clears ambient environment before the shell AND before Python,
requires all three descriptors to be TTYs, real/effective UID501, Darwin/arm64,
sets core dumps off and umask077 only for the dedicated child, and checks exact
file type, no symlink leaf, size/mode/owner/link count and SHA256 for the frozen
Python3.14.7, helper, system hdiutil and diskutil. The helper independently checks
source identity before native actions and before manifest publication.
Expected runtime and utility values were read back locally without executing
Python's helper or either disk utility. Source hashes are external review
bindings, not a claim that hashing a file authenticates itself.

The exact isolated Python startup environment reuses the reviewed snapshot
renderer: fixed private runtime library paths, HOME=/dev/null, no Azure/Docker/
GitHub/session variables, and no user Python/site imports. The shell's own added
PWD/SHLVL are removed by the second env. Native disk utilities independently get
the helper's fixed clean environment and inherited Terminal stdin. Native
hdiutil, not Python, handles passphrase input.

Trusted OS/system utilities and same-user integrity remain assumptions. These
shell checks are not descriptor-atomic protection against malicious root/sameUID
replacement, nor independent full dynamic-library execution attestation. This is
EMPTY workspace setup, not permission to copy credentials/data. Later snapshot
startup independently binds its complete native/runtime/source closure and the
actual encrypted mount before any credential/session/data operation.

## Outcome, failure and later gates

Success is the helper's actual exit0 and final empty-workspace JSON with the
new path and manifest_sha256. A leftover file/path is NOT success. The public
manifest is local evidence, not Azure identity, a receipt or permanent readiness.
The operator may report only the workspace path and manifest hash; never the
passphrase. On refusal, report its fixed code/path, not a full operational log.

Do not automatically rerun, detach, delete, overwrite, adjust permissions or
clean up a partial image. Retain the exact path and diagnose before any separately
scoped recovery. Native prompt timeout is300seconds per create/attach command;
fixed metadata reads are30seconds. Do not describe this as one aggregate300second
deadline. This operation creates no database data.

No ten-minute clock starts here: empty encrypted setup was explicitly separated
from the short live-action window. Actual source/identity preparation, independent
exact plan review, fresh ten-minute handoff/command review, human firewall/capture,
actual-source isolated ONLINE four-migration rehearsal and exact staging release
still follow. No source review, shell syntax check, file hash or helper output
proves a live snapshot, restored data, migration, staging URL, Clerk or Zoom.

## Verification scope

Validate the outer shell and extracted inner -c text with /bin/sh -n ONLY;
parse/check the exact argv/environment and file-check manifest as data.
Do not execute this .command file, its shell body, helper, hdiutil or diskutil.
Unchanged source tests are reused from their exact reviewed versions; no test,
paid Actions or native disk operation is replayed for this documentation change.
Independent exact command review must be tied to the actual final command bytes
and source head. Record that review before giving the command to the human.

Recorded static validation at2026-09-15T15:00:37.374233UTC: both outer/inner
shell syntax checks passed; all four exact file/hash/mode/owner/link checks
passed; parsed final argv and environment equal the reviewed pure startup
renderer. Command3673bytes, SHA256:
7a6b2ab037da2b065481621779a9c4ddc56e29a532979cde9794d3279ee9669b.
No command body, workspace helper or native disk utility executed. No human
passphrase, source review verdict or native encryption outcome was fabricated.
