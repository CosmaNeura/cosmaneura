# Diagnose staging Scribe upload 503 responses

Tier 2 because this touches capture and encounter request handling. No authority,
session, schema, configuration, dependency, request or response contract changes.

The owned iOS12 synthetic run uploaded eight of eleven sealed silent chunks before
showing its retry state. Four native 503 responses correspond within approximately
50 milliseconds to Core web67 capture-operation/encounter 503 logs. Existing logs
contain no exception category or SQLSTATE, so they cannot establish the cause.
The previous receiver correction preserves the 503 instead of converting it to
403; it does not repair the underlying Core failure.

Add staging-only diagnostics at the two existing unconfirmed-error catches.
Emit fixed categories for boundary/operation/error type, allowlisted SQLSTATEs,
and source module/line locations. Bound exception chains and traceback traversal.
Never render exception messages, request values, headers, IDs, SQL, parameters,
locals, source text or full stack traces. Keep every response, retry indicator,
authorization check and client close unchanged. A diagnostic failure emits nothing
and must not replace the original response. Production emits no added event.

Validation exercises real route functions with isolated synthetic dependencies,
unchanged HTTP bodies/headers, client cleanup, bounded/cyclic causes, rejected
metadata and an exception whose string conversion must never run. No model calls
or live authority requests occur in those tests.

After independent final-head review and merge, prepare a separate narrow web-only
staging rollout based on current trunk. Reconcile deployed web67, retained worker,
Session Prep, configuration and schema first. This document does not perform a
rollout. Reproduce only with the owned synthetic account and correlate the new
event with existing request metadata; a log event is diagnostic, never a passing
upload result. Recover by reverting these diagnostic additions on current trunk,
preserving later changes and all recordings, queues and database schemas.
