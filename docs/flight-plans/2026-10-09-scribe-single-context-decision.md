# Scribe capture: derive current authority after its locks

Risk: Tier 2 (shared authority/session code). Owner scope: Scribe desktop and
mobile startup <=5 seconds; September 25 local validation and implementer-review
merge authority applies. Existing staging release authority remains bounded to
Scribe latency work. No production release or migration is included.

The current capture scope derives complete context, locks/refreshes every
authority input, then derives the same context again. Move those original locks
into a private synchronous callback before one complete context decision.
Every admission/operation phase still authorizes freshly, including after remote
I/O. Keep bearer -> Practice -> Doctor -> view-as -> token -> lineage -> mount,
actor, identity, tenant, person binding, person and membership lock order.
Recheck token and absolute session expiry after the context locks can wait.
Other callers omit the callback and keep their existing path. No permissions,
decisions, context, consent, keys or locks are reused across transactions.

Acceptance: controlled PostgreSQL revocation/lock-order/current-state/consent/
lost-reply tests; shared authority callers and applicable native capture suite;
Ruff, Pyright and implementer review of the exact tested commit. Six-stage
fictional source profiling currently reduces desktop SQL 877 ->751 (126), and
native phone preparation 392 ->356 (36). Numeric local profile times include
instrumentation and do not establish device speed. Actual first-input timing
is still required after staging release.

Release: build the exact merged candidate with the retained staging controller,
qualify the immutable image and existing virtual audio modalities, observe source
and schema/configuration drift, then replace only the existing staging role
images. Preserve each role's configuration, secrets and recovery revision.
Recover by restoring the immediately prior role image/revision. No schema change
or credential operation is required.
