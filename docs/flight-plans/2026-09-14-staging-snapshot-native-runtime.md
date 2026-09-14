# Sealed snapshot native reads and immutable runtime binding (Tier 2)

## One dormant outcome

Make protected file reads and revalidation of the EXISTING relocated CLI runtime
compatible with the snapshot process seal. No new Azure resource, role, network,
dependency, CLI invocation, cache discovery, database capture, migration, or
human launcher. No create/delete/cleanup/claim operation. Direct execution
refuses. Source preparation is not a deployment or execution authorization.

Stacked on PR397. PR398's remaining live integration hold is not resolved just
by merging this component: an owned CLI/configuration session and exact human
bootstrap must connect these primitives without bypassing existing protections.

## Native and file boundary

After irreversibly lowering both core limits to zero, preload the protected
macOS libSystem ACL symbols and native errno pointer BEFORE sealing. Subsequent
ACL calls never resolve ctypes libraries/symbols or call audited Python errno
helpers. The pinned native closure must not spawn workers. Linux checks reject
POSIX/NFS ACLs. This is not a malicious in-process/native-code sandbox.

All reads require PR397's fresh dedicated isolated/no-site single-thread seal.
Hold every root-to-parent directory descriptor using parent-relative NOFOLLOW
opens, validating owner/mode and rejecting allow ACLs on all ancestors. Only
root-owned sticky intermediate directories may be public-writable; immediate
parent is not. Ordinary intermediate deny-only macOS home ACLs are permitted.
Recheck the held chain against parent-relative names before returning, and
close every owned descriptor in short masked teardown, not a long masked read.

Secret-file reads require one owned regular0400/0600 file, one hard link, no
ACL, private0700 parent, bounded positive size (at most8MiB), stable device,
inode, owner/group/mode/link count/size/mtime/ctime and parent binding. FIFO,
symlink, hard link, rename, read-time mutation or timeout refuses. Content is
repr-hidden; failures have one fixed public code. No discovery or file write.

## Existing complete runtime closure

Use only the twelve fixed existing Homebrew formula/version subtrees and exact
opt aliases already specified by the normally merged validator. Private copied
runtime directories/executables are0500, other regular files0400; links must
stay within the runtime root, never absolute/external. Reject extra top-level,
formula/version/opt entries, unsafe types, ownership, ACLs, hard links and drift.
At most50000 manifest entries/768MiB; individual file at most128MiB; depth64.

Full content/type/executable/link hashing uses the existing validator's exact
canonical manifest representation. The expected snapshot digest MUST come from
independently reviewed source-derived preparation/plan. Computing a hash from
arbitrary current files and treating that as its own accepted pin is forbidden.
The full protected source/bootstrap and loaded class identity remain separate
gates. Paths alone and this class alone do not attest those gates.

Full hashing happens once before interaction; publish the in-memory binding
only after all early entries' metadata is checked again. Do not silently
rebaseline. Subsequent owned-child checks, inside their original active timer,
compare the complete retained file/link/directory namespace and metadata,
including ctime, before AND after the child. Do not use only mtime or only the
executable. ACL/chmod/in-place/replacement/namespace edits refuse; observed drift
requires fresh source-derived preparation/review. This optimization needs exact
independent review. No atomic exclusion of malicious same-UID/kernel edits is
claimed, and no changed runtime may be blessed by a metadata refresh.

## Verification evidence and limits

Native draft: three new ancestry regressions plus six affected methods checked;
all nine PASS after correcting the deadline test's expected descriptor count
for the newly held ancestor chain. Runtime draft: eleven new methods PASS,
covering whole-tree manifest, wrong pin, permissions, links/FIFO, in-place edits
with restored mtime, transient namespace edits, change during initial scan,
unbound/untimed checks, and actual deadline descriptor cleanup.

After repository relocation, two affected import/child-launch regressions PASS
from a shared unsealed unittest runner. Other passing test bodies not replayed.
Ruff/Pyright/diff checks pass. All fixtures invented; no actual account/cache/
CLI/network/database/encrypted workspace is used by tests. CI and exact
independent source review remain required on the final head.

One separate read-only inventory of the existing immutable homebrew subtree
observed36268 manifest entries,36283 retained metadata entries,585099763bytes;
hashing4.403seconds, metadata recheck0.175seconds. Observed manifest SHA
4dc3443055fef6f2112c96178fd86df3bf5a675ccdfaac6cd597f2bda13ebb0f.
No binding was accepted, CLI executed, token read, or authority generated.
These timings/pins are diagnostic evidence, NOT a fresh execution plan.

## Recovery and remaining gates

Stop using this dormant component; it has changed no deployed state. No data
or resource cleanup, permission weakening, automatic rebaseline, forced merge,
receipt renewal or retry is a recovery action here. Human still creates/removes
the separately approved single-current-IP rule within30minutes and accepts
concurrent-edit risk; no helper firewall mutation or native TTL/crash guarantee.

Still required: safe owned CLI/configuration and explicit account/exact-token/
MSAL-home linkage, actual protected human/native encrypted workspace/public-IP
binding, normal protected source merges when separately approved, exact fresh
plan (actual4h timestamp) and10minute handoff independently reviewed after real
Terminal-presence confirmation. Then human snapshot, actual isolated restore
and four migrations, staging release, Clerk/Auth, ordered Zoom acceptance.
