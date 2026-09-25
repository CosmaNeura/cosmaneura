# Invocation-owned staging migration job

Tier 2: a staging deployment security boundary. The authorized scope is local
implementation and synthetic tests, followed by a separate independent review.
This change does not authorize a deployment or migration.

The deployer currently deletes and recreates a shared migration job. A separate
invocation can start using that job after an earlier absence check. Generate one
OS-random `cn-stg-mig-` name with 80 random bits per deploy invocation and retain
it for creation, schema probes, migration execution, terminal-state observation
and cleanup. Refuse an observed collision without replacing the resource. Claim
cleanup ownership only after creation succeeds; retain an ambiguously failed
create for explicit operator reconciliation under the printed job name.

Pass the generated name as explicit context to the token-bound Azure CLI. Job
names, container names, absence queries and execution prefixes must match it.
Older CLI callers retain their fixed legacy job default; the deployer always
uses a newly generated name. Candidate, plan v1/v2, publication, schema, image,
identity, production exclusions and separate Scribe target contracts stay intact.

Local regressions execute the actual shell helpers and bound CLI entry point
with synthetic credentials and a fake Azure provider. Cover two independent
invocations, collisions, failed/ambiguous creates, schema probes, timeout stop,
cleanup failure blocking rollout, and rejected foreign selectors. No test calls
live Azure, Docker, a browser or a real database. Unique jobs isolate resource
ownership; they do not serialize concurrent database migrations.

Local validation passed 323 tests across the ten affected infrastructure suites,
scoped Pyright and Ruff checks, Bash syntax and diff whitespace checks. This is
synthetic provider evidence, not a live Azure rehearsal or migration result.

Recovery: stop a failing deploy before workload updates, retain its exact job
name and observed status, and reconcile only that invocation's resource. Never
delete the shared legacy job or another invocation's job as recovery. Existing
application revision recovery and additive schema retention remain unchanged.
Independent review must bind the final substantive commit before merge.
