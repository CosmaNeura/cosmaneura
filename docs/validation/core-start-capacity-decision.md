# Measure dependencies before changing capacity

Enable only the existing staging observer: PYTHON_ENV=staging and
SCRIBE_NATIVE_REQUEST_TIMING=true. The closed JSON console records already flow
to the environment's Azure Log Analytics workspace. No Application Insights
service, auto-instrumentation or paid resource is introduced. Use the accompanying
KQL with bounded fictional test windows and exact deployed revision.

Inspect separate consent/session/metadata dependency totals, Clerk/workload/policy
phases and SQL cursor work. Nested phases overlap; their totals cannot be added.
SQL excludes connection acquisition/pre-ping/commit. Zero absent-phase values are
not proof of absent work. Failed/timed-out observations must be considered with
successful observations, not removed from latency percentiles.

Match that time window with existing Container Apps CPU, memory, replicas and
restarts, and PostgreSQL CPU, active connections, locks and storage latency.
Increase CPU only if request-tail latency coincides with saturation; increase DB
capacity only if SQL/pool/lock observations and database metrics agree. First
remove repeated network work when dependencies dominate. One-variable staging
comparisons need the same source, fixtures, request boundary and revision.

The measured Scribe receiver access-check SQL was 0.212–0.222 seconds per virtual
Start. It is not a measurement of all Core SQL and does not support buying more
database capacity. Current capacity is retained. Record full consent-handler to
retained native audio separately from these server-only observations.
