# Scribe one creator preparation read

Tier 2: creator/consent receipt and provider authority path. This is part of the
owner's Scribe startup repair goal and approved backend staging latency release.
Local checks, implementer review and passing merge follow the September 25 owner
decision.

The existing combined native metadata operation still makes five Scribe reads:
three creator reviews, a wrapping-key read and a capability read. Consume the
additive Scribe preparation receiver in PR 291 instead. It verifies the configured
cohort, live consent and creator before key I/O, releases its database transaction,
then verifies current consent/creator again and assembles all three existing
receipts in one final transaction. Core retains fresh authorization before and
after remote I/O, validates each nested receipt, and preserves its final native
scope check. No transaction spans external I/O. No permission, token, schema,
configuration, cohort, dependency, billing or production change is included.
The read grants no recording permission; subsequent capture mutations retain
their existing checks and immutable recovery.

Use the task-owned synthetic PostgreSQL lab for actual native receipt/query
comparison, lock order, committed revocations, deadline/cancellation, session
replacement and malformed receipts. Verify one actual HTTP request with the
existing workflow client. Scribe PR 291 separately qualifies the actual receiver
with consent revocation during both key retrieval and provider closure, no
database connection held across either, and unchanged direct receipts. Preserve
timeout and denial coverage despite the lower statement count. Record the exact
tested/reviewed commit and findings in the PR before merging.

Release Scribe API PR 291 first with the existing immutable image publisher and
image-only staging adapter. Then release this Core consumer with the existing
source-bound staging controller. The native receiver and installed client already
support the closed metadata result and need no package change for this step.
Verify source/digest, readiness, unchanged configuration/secrets/schema and
retained recovery images. Recover Core before restoring the previous Scribe API;
all prior direct endpoints remain compatible. Measure installed preparation
latency after both are delivered. Local statement savings do not establish the
owner's 5–7 second goal. Audio verification stays in the virtual environment.
