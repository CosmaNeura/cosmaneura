# Restore Python quality checks for the current note candidate

Risk: Tier 2 because capture and clinical note paths receive annotation repairs.
The current candidate fails 25 Pyright diagnostics, 14 import-order checks and
four formatting checks. Keep the repairs limited to current Zoom/capture code
and its synthetic regression fixtures; do not change clinical source selection,
authorization, prompts, stored data or source resolution semantics.

The schema helper accepts the fields/schema protocol actually shared by the
two history implementations. The bot's existing payload indexing is explicitly
typed without altering the indexed value. The context schema's heterogeneous
JSON map is annotated. Register the existing Celery task through its supported
decorator form with the same name, zero retries and empty auto-retry tuple.
Tests retain their source/negative assertions, real enum values and typed
transcript/binding fixtures. Import sorting and formatting are mechanical.

Run complete Python lint/format/type checks, the affected Zoom/capture regressions,
and actual PostgreSQL note-policy/binding cases. Confirm broker routing and task
retry configuration; inspect source and lock changes independently after the
final substantive push. No dependency, schema, migration, environment or cohort
change applies. No live model/provider evidence is inferred from synthetic tests.

Include only the independently reviewed commit in the next exact candidate.
Recovery is a reviewed revert of this source commit or the previous compatible
artifact. Retain all database state and existing authorization/activation controls.
Production promotion remains a separate founder-only manual decision.
