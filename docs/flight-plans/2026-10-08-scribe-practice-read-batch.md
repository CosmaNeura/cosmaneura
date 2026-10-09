# Scribe fresh practice read batching

Tier 2: provider-record authority path. Owner-directed Scribe startup latency work;
local checks, implementer review and merge under the September 25 owner decision.

## Change and boundaries

After the existing fresh doctor-access decision, read the membership and its
practice with one outer join instead of two database round trips. Refresh both
ORM rows. Keep membership-state and missing-practice refusals, all downstream
authority decisions, transaction boundaries and row-lock acquisition unchanged.
The join observes one committed state for both rows; it does not reuse an earlier
access decision or add any cache. No schema, API, dependency, permission, session,
cohort, environment, billing or production change.

## Qualification

Use only the task-owned synthetic PostgreSQL database. Compare actual native
creator-review and preparation-metadata receipts, downstream calls and exact lock
order against the preceding separate reads. Cover normal and denied access,
stale ORM rows, missing membership, and a committed membership revocation after
the initial access check. Run applicable capture, deadline, receipt, gateway and
provider-authority regressions; record the exact final tested/reviewed SHA in the
PR. Preserve timeout coverage tests independently of the reduced query count.

## Release and recovery

Merge passing work, then use the already approved immutable image-only staging
release methodology. Verify the selected source, image and preserved role
configuration before release. Production remains outside this approval. No
migration is needed. Recover by restoring the preceding verified staging role
images. Retain the current image until its successor is qualified.

Measure installed preparation latency after release; synthetic query savings are
not a claim that the desktop 5–7 second target has been achieved.
