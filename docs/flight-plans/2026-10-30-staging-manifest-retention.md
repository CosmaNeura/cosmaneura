# Staging candidate manifest retention

Tier 2: this changes the credential-bound ACR command allowlist, a security
boundary. The source-only change is based on merged Core
`9a6fc4635c81e300312f44f212390d03f19e6c72`.

## Problem and scope

The local staging publisher protected the exact candidate tag, but its
digest-addressed manifest could remain writable and deletable. Publication
must protect both objects and verify their actual attributes before recording
success. Read and list access must remain enabled.

The bound CLI admits exactly two additional operations on the existing staging
repository: a digest-addressed update setting only write/delete to `false`,
and a metadata read for that digest. Before either operation, the existing
credential-bound CLI reads the exact candidate tag and requires its name and
digest to match, write/delete to be boolean `false`, and read/list to be boolean
`true`. A mismatch, failed lookup or malformed readback prevents the operation.
An arbitrary digest alone does not authorize a manifest operation.

The publisher preserves its tag lock and readback, then locks the manifest by
`repository@sha256:digest` and reads that manifest's metadata. Success requires
the exact digest and all four expected attributes. No new identity, credential,
dependency, workflow, runtime, configuration, feature flag, schema, production
path or registry-wide setting is introduced. No operation can enable write or
delete access.

## Validation and release gate

The 66 focused offline infrastructure tests passed. Invented ACR readbacks
exercise candidate/digest binding, rejection of broader commands and unlocked
tags, lookup failure, and suppression of a success receipt when manifest
protection or read/list access is missing. Ruff, Pyright, Bash syntax and diff
checks passed. Installed CLI source confirms digest references use the manifest
endpoint and that command output can be captured for the preflight readback.
These checks do not establish live registry protection or deployment readiness.

After the final substantive push, an independent same-task reviewer must
review the exact remote commit and record findings and dispositions. Any later
staging publication remains a separate authorized action with actual tag and
manifest readbacks. This source checkpoint does not merge or deploy anything.

## Recovery

A failed protection check stops publication before its success receipt. Keep
existing tag and manifest locks intact; do not weaken them to bypass a failure.
The existing approved immutable image remains the runtime recovery target.
This source change performs no runtime rollout and needs no database rollback,
new image, credential change or production action for recovery.
