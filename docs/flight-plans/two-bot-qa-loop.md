# Two-bot QA loop adoption

Owner: Shawn / release owner. Risk: Tier 0 for the additive local tooling and
documentation here; reclassify activation and any later environment/build work
by actual effect. This is a cross-repo process adoption, with no runtime contract
or schema changes and no receiver ordering requirement.

Outcome: finite synthetic browser acceptance gates, independent defect retests,
and a durable local ledger alongside existing tests. Both repos start paused.
No application paths, dependencies, environments, CI or deployment workflows change.

1. Land local ledger, protocol, templates, tests and independent review in each repo.
2. Owner supplies bot identities/private credentials and selects isolated targets.
   Review synthetic-only, tenant boundaries, outbound mail, provider test mode,
   frontend/backend build observability and evidence redaction before activation.
3. Create authorized GitHub queues/labels. Source-review and populate one pilot
   per bot; observe manual runs and verify results before scheduling.
4. Select/authorize a runner and scheduler; rehearse locks, crashes, pause,
   retry reconciliation, disk monitoring and private log retention. Keep Actions
   disabled in Scribe. Automated deployment is excluded.
5. Expand only to the finite gates in PILOT.md. Owner-controlled deployment and
   independent review remain mandatory. Park unavailable features explicitly.

Recovery: pause both queues and threads, stop scheduling between runs, retain
history and evidence, revert additive files if needed. No migration rung or
feature flag applies. There is no deployed change to roll back.

Acceptance: local regression suite passes in both repos, independent agent
review records exact content and findings, and no browser gate is claimed passed
until a bot produces evidence against the configured immutable release.
