# Source-grounded Zoom note rollout in three reversible changes

Tier 2: clinical source attribution and note content. No migration, new service,
authorization expansion, production change or deployment is part of these PRs.

1. Preserve distinct unverified source labels for non-two-source recordings and
   add dormant eligibility checks for the existing exact staging lab A/B cohort.
2. Add the dormant source-grounded adapter, source rendering and component tests.
   Ordinary summary jobs have no call path to it at this stage.
3. Wire only eligible durable Recall ingestion into the guarded adapter, using
   one configured generation attempt and existing tenant/capture/replay checks.

The final runtime must remain byte-identical to evaluated R16 b4c3f987. Existing
user fields, examples and preferences remain inputs. Unknown role labels are not
acoustic diarization. The malformed R7 ASR numerals remain source text, not an
interpreted blood-pressure measurement. Quoted questions are not clinical findings.
Structural and/or context retention intentionally keeps some fully represented
questions; it never claims an answer is missing. Every substantive final push
requires independent review tied to that head; review is not GitHub approval.

Preserve every prior failed model result and regression assertion. Local replay
of unchanged R15 responses with identical requests tests deterministic R16 source
retention, not a new model result. Final PostgreSQL checks cover scope boundaries,
actual generation, exact saved fields, unchanged transcript and no-call replay.
A separately selected live bot run must still qualify the merged staging release.

Recovery: before activation, leave helpers/policy dormant or revert their PR.
After activation, revert the dispatch PR so new jobs use the existing default
path; existing notes and transcripts are not rewritten. Revert the independent
role-label change separately if required. Deployment, cohort expansion and any
production decision require their existing owner approvals.
