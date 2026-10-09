# Capture requests must not stall their server instance

Risk: **Tier 2** — capture request lifecycle and clinical publication boundaries.

A synchronous SQL wait in an async capture route prevents other requests on that
instance from progressing. Async timeouts cannot interrupt it. The regression
uses the actual route, gateway, admission transaction and middleware with an
injected SQL wait; the unrelated read and second capture request stall on the
unmodified source.

Own each selected capture request in a private thread and event loop, including
its dependencies, session lifecycle and response middleware. Bound admission to
four workers per process and the public request to 90 seconds. Retain capacity
until the worker and transport cleanup settle. Keep the session on its owning
thread; never detach work that borrows another request's session.

Set transaction-local PostgreSQL lock waits to at most two seconds and statement
timeouts to the remaining request budget. Recheck the budget before SQL flush,
commit, new provider/model dispatch and acceptance of remote results. A timed-out
request cannot admit later publication. Work already accepted remotely remains
subject to the existing exact-key receipt recovery; this change adds no retry.

The five existing capture POST routes retain their authentication, consent,
identity, request and success-response contracts. Other routes do not use capture
capacity. No schema, dependencies, model parameters, environment configuration or
production entrypoint change is included.

## Validation

Run with `PYTHON_ENV=test`, `--confcutdir=tests/authority_kernel`, disabled external
network access and the repository's existing synthetic authority bootstrap. Do
not invoke the repository-wide test hook, which contacts external identity and
database services.

- `scribe_capture_availability_test.py`: the deployed baseline blocks an unrelated
  read and second capture request; the candidate serves both while the first SQL
  call is waiting, times out that request, and closes both clients without Scribe
  dispatch.
- `scribe_capture_deadline_test.py`: request/session ownership, late SQL/commit,
  cancellation/disconnect, token/model fences, bounded capacity and cleanup.
- `scribe_capture_middleware_deadline_test.py`: actual middleware and final-send
  deadlines, static errors, headers, route inventory and unrelated requests.
- Existing `scribe_gateway_receiver_test.py` and `scribe_workflow_test.py`:
  request/response and provider transport compatibility.

Record commands, outcomes and exact reviewed commit in PR evidence. Independent
review occurs after the final substantive push. These checks establish the local
mechanism and regression, not successful operation on the installed iPhone.

## Release and recovery

Use the existing founder-selected, merged-commit staging procedure. This local
change grants no deployment or environment-configuration authority. Reconcile the
current staging SHA/image before selecting a candidate; do not replace intervening
changes blindly. Verify the retained synthetic recording through transcription
and saved-note reopening on the phone. Recover by selecting the prior reviewed
Core artifact through the same release procedure; no migration rollback applies.

The original upload incident's triggering wait is not established. Its Core
instance was replaced before direct inspection. This change fixes the separately
reproduced amplification of a blocking capture call. Keeping the native receiver
warm addresses a distinct measured cold-start timeout and is a separate decision.
