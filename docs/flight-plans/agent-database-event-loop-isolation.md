# Agent database waits must not stall the ASGI event loop

Risk: **Tier 2** — authorization transactions and request-session handling.

## Problem and change

The async agent endpoints call synchronous SQLAlchemy authorization and write
services. When one waits for a Doctor row lock, it blocks the ASGI event loop,
including health checks and teardown of other requests that may hold that lock.

Run the synchronous service calls through Starlette's existing threadpool helper.
For Session Prep, keep authorization, invocation, and its existing commit inside
one sequential worker call. Body reads and validation remain on the async route.
The threadpool propagates the request's ActiveModel ContextVar. An owned asyncio
task shields its worker and drains it before re-raising cancellation, including
repeated direct `Task.cancel()` calls, so request teardown cannot close the shared
session while the worker uses it. Preserve Doctor
and view-as locking, authorization checks, transaction boundaries, error mappings,
and response contracts. No dependency, pool-size, protocol, or schema changes.

## Validation

Use `--confcutdir=tests/authority_kernel` to avoid the repository's external test
bootstrap. `agent_database_responsiveness_test.py` requires an explicitly owned
loopback PostgreSQL URL in `AGENT_STALL_POSTGRES_URL`, restricted to the synthetic
`agent_db_stall` database. It does not query application environments.

Before the fix, all ten route/error combinations reproduce a PostgreSQL Doctor
lock wait that prevents health responses until an independent holder releases
the lock. After the fix, health responds while the lock is still held. Tests also
check the same request session, no early commit, denial/error rollback, and lock
reacquisition after teardown. For every affected route, AnyIO scope cancellation,
direct `Task.cancel()`, and repeated direct cancellation verify that a running
worker retains its transaction until completion, the first cancellation is
preserved, and teardown rolls back its uncommitted write and releases the lock.
The direct cancellation cases reproduce premature session closure with the
unshielded threadpool await. Retain the
affected mount, turn, audit, usage, and Session Prep contract suites.

## Release and recovery

Require independent review after the final substantive push. Deploy only the
reviewed merged artifact under the existing staging authorization. Verify health
concurrently with the synthetic Session Prep workflow and inspect bounded lock
waits. Recover through the reviewed paired Core image/revision procedure if the
candidate regresses. No schema migration or data rollback is needed.

This fixes the reproduced event-loop amplification mechanism. It does not prove
which live request originally held the Doctor lock, remove intentional
authorization serialization, or impose new database lock/statement timeouts.
Cancellation cleanup waits for the synchronous worker to finish; it cannot make
forced process termination safe or interrupt a database call already in flight.
