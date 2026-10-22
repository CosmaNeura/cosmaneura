# Provider receiver admission isolation

Tier 2: provider Session lifecycle and request cancellation. This local stacked
increment starts at PR811 `b55535fca1775a5ab3575bccf2fa55c58b675f52` and does not
change that reviewed head or the prepared `af6113ea`/Sovereign86 rollout.

The existing twenty-second cooperative database checkpoint budget starts after
AnyIO thread admission. Starlette uses AnyIO's shared default limiter, whose
forty tokens can be occupied by other work. Caller timeouts do not reclaim a
retained worker still waiting for that token.

Only the existing trusted Sovereign workload, with authority enabled, reserves
one of two provider slots. The unshielded one-second deadline covers this
explicit acquisition alone. Queue expiry returns the existing value-free
unavailable category; queued cancellation propagates without a worker or Session.
After reservation, public AnyIO thread dispatch uses a private bypass limiter so
it cannot acquire a second slot or wait on the shared default limiter. This is
admission isolation, not a private physical threadpool. The default limiter and
all nonprovider/Scribe admission and Session behavior remain unchanged.

A locked cancellation/start gate runs before the owned Session and body. A
cancelled, submitted-but-unstarted wrapper cannot dispatch that work later.
Started work retains its slot until Session cleanup finishes, including repeated
caller cancellation. Ordinary exceptions and the original cancellation are
preserved. Limiters are lazy per event loop and replaced in the forked child.

Secretless local tests use installed AnyIO with all forty shared tokens occupied,
two provider slots exhausted, queue timeout/no late dispatch, cancellation before
and at start and through actual Psycopg socket checkpoint cleanup, normal errors,
ordinary workloads, and loop/fork lifecycle. No database, model or external call
is required. These do not establish a hard CPU/OS/remote-COMMIT deadline, physical
thread creation bound, or live failure recovery.

No dependencies, settings, schema, source cohort, clinical protocol or Scribe
paths change. Independent final-source review is required before any push/merge.
Recovery is the prior compatible frontier-aware image, retaining canonical and
correction-frontier data. Reverting removes admission controls; it is not proof
of their runtime acceptance. Publication/deployment and actual PG acceptance
remain separate decisions and evidence.
