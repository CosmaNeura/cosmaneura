# Recovery runtime pin refresh

Tier 2: exact runtime authentication changes; local qualification only.

The recovery launcher pins Apple Command Line Tools Python 3.9.6. The installed
CLTools_Executables package is 27.0.0.0.1788430756. Its native binaries changed,
so nine positive local recovery checks refused the old reviewed hashes.

Before refreshing pins, verify every Mach-O component against Apple's signing
anchor, verify root ownership and non-writable source paths, and produce the
private readonly copy using the launcher's exact snapshot commands. The local
qualification verified 77 native signatures. Python version, 2,006 tree entries,
zero ACLs and the owner-only readonly snapshot policy remain unchanged.

Refresh executable, framework, tree metadata, tree content and runtime hashes.
Recompute the manifest, validator and launcher bindings in dependency order;
both independently authenticated callers must pin the resulting launcher.
Keep all ownership, ACL, path, isolated-execution and closed-writer checks.

Rehearse the plan, handoff and consumer modules locally, including altered
runtime/source bytes, retained writers, permission changes and forged objects.
Record independent review of the final source and the qualification evidence.

Recovery: revert this isolated pin refresh to restore the prior exact runtime
binding. The prior pins require their matching runtime; rollback does not make
the currently installed binaries pass. Old plan/handoff hashes do not authorize
the refreshed binding and must be regenerated before any ordinary founder-only
deployment decision. This change performs no deployment or Azure mutation.
