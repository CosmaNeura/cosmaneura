# Provider receiver connection waits

Tier 2: provider authority, database transactions and request cancellation.
This isolated increment starts from Core `af6113ea7a6c5dbf2a0e9bee2fa81aace2fd32d6`.
It is not part of that already prepared clinical staging rollout. No source
merge, image publication, deployment, configuration or schema change is implied.

The existing provider receiver installs one-second PostgreSQL lock and
three-second statement limits after acquiring a connection. It previously used
the shared activemodel pool and could continue waiting on a connection or lost
socket after its HTTP caller cancelled. Preserve the existing full authority,
post-lock expiry, canonical admission/replay and correction-frontier checks.

Only the already authenticated, configured dedicated Sovereign workload uses
the new lane while the authority kernel is enabled. Other workloads retain the
original request session and global engine. This is not evidence that external
workload credentials are unshared. No browser claim selects the lane.

The lane owns a two-connection SQLAlchemy pool with no overflow, one-second pool
and engine-initialization-lock waits, and the original JSON serializer. Its
Psycopg Python-only connection options are separated from libpq parameters.
SQLAlchemy's adapter context is copied with `AdaptersMap(context.adapters)`;
autocommit, prepared-statement threshold and row/cursor factories retain the
installed Psycopg API semantics, with the default tuple-row factory. An actual
offline SQLAlchemy-dialect regression checks Pydantic JSON serialization through
the copied adapters. This addresses a local integration failure before TCP;
it does not establish a successful configured PostgreSQL Session or live behavior.
The connection uses nonblocking connect/poll hooks with a shared three-second
connect budget, retaining a tighter explicitly configured connect timeout.
A literal IP skips DNS; a hostname has a one-second caller wait
and at most two daemon resolver calls. If an OS lookup remains stuck, its permit
remains occupied until it returns; subsequent calls fail closed rather than
creating more threads. The selected address is supplied as hostaddr while the
original host remains present for TLS identity. Implicit service/host discovery
and configured multi-host retries fail closed in this lane.

The request's retained database worker temporarily binds its owned Session to
the existing activemodel ContextVar, then restores the original session. Nested
authority/model calls share the same owned transaction. An already active or
dirty original session cannot be moved. Resolve now uses the same existing
retained-worker helper as bootstrap, canonical phases, tools, history and
observational reporting; its route name, parameters and public schema remain.

Query, commit and rollback socket polling has a three-second budget and checks
the cancellation signal before advancing a command generator. Each database
checkpoint also observes a shared
twenty-second receiver budget. Expiry discards the owned connection and returns
the existing value-free unavailable category. Ordinary PostgreSQL errors retain
their original rollback and integrity-conflict path. No mutation or model retry
is added. A lost commit response remains ambiguous: connection disposal never
claims that PostgreSQL cancelled, rolled back or failed to commit. Existing
same-key canonical replay and observational closure remain authoritative.

Caller cancellation signals the retained worker, which closes an uncertain
connection and finishes session cleanup before the outer request session closes.
It still propagates the original cancellation. Fork handling replaces only the
owned pool without closing inherited parent connections; no global/Scribe pool
is disposed. Session setup failures restore both ContextVars.

These are bounded pool/DNS-caller/nonblocking socket waits, not a hard CPU or
operating-system deadline. Python cannot preempt a stuck resolver or a library
call that never returns to polling. The twenty-second budget is checked at
database checkpoints, not proof of a whole HTTP-handler deadline. No remote
transaction cancellation, production connection behavior, real clinical
generation or live failure recovery is established by local checks.

Use a secretless local runner that excludes global application/conftest startup.
Behavioral checks exercise the real QueuePool, actual libpq SSL-handshake stall
on one inert owned listener (no SQL or PostgreSQL server), owned socketpair
waiting/disposal, DNS permit exhaustion, same-session nested calls, cancellation
of the actual retained helper and rollback-safe integrity errors. The inert
listener must observe its literal SSL request; a generic failure is not proof
of delivery. Preserve failed initial harness/binding observations separately.

No dependencies, migrations, cohort, panel contract, Scribe route or environment
flag change occurs. The private branch is committed only after local checks;
the parent reviews scope before any push or PR. Any later source integration
must preserve the actual latest clinical/Scribe source and receive independent
review after its final substantive push. Receiver-only application recovery is
the prior compatible frontier-aware image, retaining every canonical row and
frontier. Reverting removes these wait controls; it is not their acceptance.
