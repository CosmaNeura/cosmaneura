# Preserve the raw database URL during staging migration startup

Tier 1; migration-controller correction for the exact previously selected PR #385 image only. The image, source, four migration revisions, schema transition, recovery digest, credentials, and production deployment path remain unchanged.

The September 17 staging migration failed before Alembic configured its engine: importing `app.models` first initializes the application and checks PostgreSQL's version. Pre-escaping the job's `DATABASE_URL` corrupted the driver's TLS certificate path. Both observed schema revisions remained `89c31f0ae275`; the transient job was removed and workloads retained their recovery image.

Keep the job-only compatibility secret raw, like the probe/workload URL. Its exact, template-verified Python command wraps only `Config.set_main_option("sqlalchemy.url", ...)`, then invokes Alembic `upgrade head`. Application startup receives the original URL, while the old candidate's later ConfigParser boundary receives escaped percent signs. This bootstrap must not be used with an image containing the permanent `migrations/env.py` fix; the existing candidate SHA/digest gate prevents that expansion.

Validate the actual Bash job arguments and both real SQLAlchemy URL consumers, including percent-encoded password/certificate components and a percent-free URL. Require exact bootstrap matching, unchanged production command behavior, and independent review of the final commit. Normally merge the controller before refreshing source/review bindings and making one new staging attempt.

Acceptance remains the rehearsed `89c31f0ae275 -> 9a2399212488` transition, terminal migration outcome, deleted transient job, healthy exact candidate revisions across API/worker/scheduler, provider-v7 smoke/status, and functional QA. On failure, stop rollout and retain sanitized evidence; observe schema only after terminal execution and do not retry unchanged. Do not drop migrated structures or promote production as recovery.
