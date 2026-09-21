# PostgreSQL client and private-library binding (Tier 2)

## Outcome and boundaries

Stacked on PR403 at fd86b3a80efdf3418d2a3c16b49170da0b5aa7d7. Bind the three
existing PostgreSQL16.15 clients and their complete recorded non-system dylib
closure before a human snapshot can access credentials. The bootstrap now
binds 13 source modules and both runtimes under its original 90-second limit.
This is not a human launcher, plan, receipt, protected-source provenance check,
independent review, or execution authority. No database or Azure request runs.

The original /opt/homebrew/opt directory is group-writable (0775, UID501,
GID80), so it cannot be trusted as a runtime namespace. Do not change Homebrew
or weaken the ownership/ACL checks. A private 8,746,944-byte copy of just the
12 existing public libraries avoids those shortcuts. No new dependency,
service, Azure resource, role, network, or ongoing infrastructure cost.

## Independent recorded content and private layout

Original complete Mach-O inventory recorded September14 at18:52:26UTC:
postgresql16-runtime-pins-v1.json, SHA256
c22dfb4dd606e45e8c14e65f1ab2d9bc9d96f68d60010623696f2a5146f33495.
It records every non-system edge for the three clients and 12 libraries,
their canonical paths/size/mode/raw hashes, and protected OS dependencies.
All15 FILE_PINS match that original inventory; no observed-copy rebaselining.

Installed clients remain at /opt/homebrew/Cellar/postgresql@16/16.15/bin,
owned by the configured local user, regular one-link0555 files. Their protected
canonical ancestor identities are retained; no opt shortcut is traversed.

Libraries are fixed to
/private/tmp/cosmaneura-staging-baseline-20260914.adNnmt/pg-libraries.
The directory must be owned0500 with no ACL, exactly12 canonical0400 regular
one-link files, and exactly these three owned single-link relative aliases:

- liblz4.1.dylib -> liblz4.1.10.0.dylib
- libreadline.8.dylib -> libreadline.8.3.dylib
- libzstd.1.dylib -> libzstd.1.5.7.dylib

No extra file, subdirectory, alternate target, alias chain, symlinked canonical
file, writable leaf, hardlink, or caller-selected directory is accepted.
Every library's size and full SHA256 must match the original source constant.
Native held-parent/ACL/no-follow reads and pre/post metadata checks apply.
The exact namespace and metadata (including ctime) are retained and rechecked;
drift refuses, with no repair, cleanup, rebaseline, or retry in that process.
This is not a sandbox against malicious same-UID/native code or root.

The OS build must remain macOS arm64 25G83. The protected root-owned CA is
/private/etc/ssl/cert.pem,333483bytes,0644,SHA256
9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994.
Both OS build and CA metadata are retained. The fixed OS library/shared-cache
boundary remains separate from the15 non-system content pins.

## Child environment

All three clients get only the fixed private DYLD_LIBRARY_PATH. There is no
inherited DYLD/OpenSSL/Kerberos/HOME/user-config setting. HOME and OpenSSL
configuration/modules are /dev/null; Kerberos config, cache and keytab paths
are disabled. Explicit UID-derived CoreFoundation encoding avoids ambient
HOME discovery. Credential-free local pg_restore decoding uses that same
closed client environment, without any PG connection variables.

Networked clients retain verify-full TLS, fixed CA/host/database, read-only
transaction options, no password file/service, disabled GSS encryption, and
the existing refusal of passwd-home .postgresql TLS material. They now require
password/md5/SCRAM authentication and disable SSL client-certificate selection
and PG system-config search. The actual pinned libpq contains these supported
option names. No live connection/authentication/TLS outcome is claimed.

## Affected verification

Nineteen closure tests PASS: exact inventory/alias layout, actual invented
protected-file binding, original opt never used, wrong hash, writable client/
parent/library/directory, extra shadow library, missing alias, unsafe/absolute/
escaping/cyclic aliases, regular alias, symlink directory, hardlinks, replaced
files/links, in-place change with restored mtime, namespace/client-parent drift,
CA/build drift, absent/expired guard, one-shot binding, short reads and swallowed
timeout. Two changed child-environment tests and the exact bootstrap dependency
pin test PASS. Initial native fixture startup was refused by the tool sandbox's
ps restriction; only the failed methods were rerun with that read-only check
permitted. No production guard was mocked away to work around the restriction.
The earlier affected composition and payload tests remain passing evidence;
unchanged passing tests were not replayed. Ruff/Pyright/diff checks apply.

Actual macOS copied-Python3.14.7 bootstrap integration PASS:13 source modules,
378 native images,15 PostgreSQL files,12 load routes,26 retained PG paths,
permanent process seal, full copied-runtime binding and retained revalidation
in5.305seconds. Public source fixture only; no account/token/network/consumer.
Native probe script SHA256:
0ceb0038f679444cede97496e834d11fb735f367011d90785deaa41b8cb41d3e.

Private-copy preparation completed23:38:36UTC, checked all original content
before/after copying, exclusive targets, no source permission changes.
Preparation script SHA256:
8174bf0863765f84680c5cda957f8a727e0bf8281e2f115bd0f9a66ba4ce7919.
At23:40:03UTC, separate offline --version-only DYLD_PRINT_LIBRARIES checks
passed for pg_dump,pg_restore,psql: all non-system libraries resolved only to
the pinned private directory. OS libraries stayed under /usr/lib or
/System/Library. The diagnostic parses dyld's observed delayed-image messages
only when their names also occur in validated image records. Its initial
parser failure was corrected before this pass. This does not prove live TLS,
authentication, dump correctness, encrypted storage, or server reachability.

## Recovery and remaining gates

Stop using the dormant code on failure; preserve partial/private artifacts,
do not delete or change source installations or QA data. No live state changed.
Exact independent final-head source review, passing protected CI, separately
authorized normal merges, complete typed Operations/current-public-IP adapter,
actual reviewed four-hour plan and fresh ten-minute handoff remain mandatory.
Neither review36 nor an older source receipt covers this new source.

No encrypted workspace, current snapshot plan, handoff or Terminal command is
ready. Human creation/removal of the approved single-IP30minute firewall rule
remains human-owned. Actual snapshot, isolated restore/four migrations, manual
staging release, Clerk/Auth and dependency-ordered Zoom acceptance remain undone.
