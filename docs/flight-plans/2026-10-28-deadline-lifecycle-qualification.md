# Complete deadline lifecycle qualification

Tier 2: qualify capture cancellation and service-identity ownership without
changing production deadline, dispatch, publication or authorization behavior.
The owner authorized this narrow follow-up on 2026-10-28; merging is handled by
another collaborator. Start from merged #877 (`539dda00`), which synchronizes
late-note/consent tests and consumes settled watcher errors. Do not duplicate it.

The remaining identity-route test assumed the SDK entered before a wall-clock
one-second deadline elapsed. Delaying private-worker startup by 1.1 seconds
reproduces all four configurations failing at `504 and entered.is_set()`: 504
is correct admission refusal, and does not prove the operation ever began.
That observation is not evidence of a production authorization/cancellation bug.

The actual hosted #869 trace also fails HTTPX `response_complete.is_set()` on
the second request: the real 250 ms error-send window can expire during a loaded
test scheduler turn. Injecting 300 ms into the synthetic 503 sender reproduces
that exact assertion in all four configurations. This is independent of the
worker-entry race and is not evidence that SDK ownership was released early.

Extract the existing test-only coordinated clock into a shared helper with an
explicit duration; the six existing capture tests retain their 80 ms budget
and original real response-send timers. The extracted default path retains the
original clock logic after substituting the explicit duration parameter.
The identity test retains its one-second budget, starts the request as a task,
waits for both SDK entry and the boundary's outcome/disconnect wait, then advances
capture expiry. This identity-ownership test additionally holds simulated time
during its synthetic error response, asserting the unchanged 250 ms configured
send window rather than using wall time from a different clock. Its real two-second
request watchdog remains active. Dedicated real response-send expiry and sender
capacity tests are unchanged. Real worker loops, cancellation, SDK joining,
SQL commit fences and bounded cleanup watchdogs remain active. Release
resistant work and join any pending request in `finally` after assertion failure.

Preserve every existing assertion: 504 at expiry, no workflow HTTP, no extra token
call, blocked capacity/503 until exact token cleanup, joined SDK ownership and
released capacity afterward. The already-merged late-note tests retain real
commit refusal and cleanup capacity checks. No production source, SDK/cache
policy, permissions, schema, dependency, cohort, environment or workflow changes.

Qualification: repeat both injected-latency reproducers after repair; exercise
the identity/capture/pool/timing suites, repeat pressure runs and run the normal
application partition on owned synthetic PostgreSQL/Redis. Report overlapping
selections separately. Require scoped type/lint checks, independent final-push
review and hosted CI. Reproducer delays are temporary local test instrumentation,
not added production sleeps or longer timeouts.

Recovery is a reviewed revert of these test/fixture changes; runtime behavior and
data are unaffected, but the scheduling-dependent test can fail again. No feature
flag, migration/rehearsal, backfill, rollout cohort or deployment applies. No real
identity/provider call, production promotion or cloud operation is authorized.
