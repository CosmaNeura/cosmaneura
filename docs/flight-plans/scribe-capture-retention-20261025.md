# Capture note retention candidate

Tier 2: clinical draft generation. Owner: Scribe/Core engineering; initial scope
is disposable synthetic evaluation only. The candidate is not qualified for
clinical use by offline prompt tests. Review the actual configured-model outputs
before a staging release decision. No schema, authority, persistence, Session Prep,
shared summarizer defaults, model deployment, or source-extractive plan change.

The recorded R2/R3 transcripts retained facts omitted by the generated draft:
prior-day headache, corrected pain score, no fall, and no arm radiation. The
capture-specific system message now asks the existing one-call generator to retain
these distinctions, preserve unknown source roles, and respect patient-only
Subjective preferences. It does not inject facts or synthesize a second summary.
Capture requests constrain the existing response envelope to the actual template's
required string fields and prospective source-reference arrays. The configured
model/deployment, one-call shape, deadlines and existing post-response validation
remain unchanged. Assessment retains only a source-stated assessment or diagnosis;
it does not generate a second history recap. A missing assessment remains explicitly
not mentioned. Structured output constrains format, not clinical correctness.

Validation requires the complete observed synthetic ASR transcripts (42 and 45
passages), three baseline/candidate repetitions each, the eight bounded semantic
cases, known-role and unknown-role checks, and the actual response envelope and
PlanGrounding resolution. The finite oracle must reject archived faulty drafts
and adversarial number, time, negation, and attribution outputs. Independent
content review is required in addition to oracle results. All outputs are retained.

Current source base: `f7af4dccd72a213b003283e8b19bebbaedf23e69` (Session Prep PR 817).
Read-only October 25 staging observation: web revision
`cosmaneura-staging-app--sp-817-f7af4dcc-web`, image digest
`sha256:07274f5e98f7a143050e36e71c1250f4cea3e4cd3662147f499ff279cea00e6d`;
worker revision `cosmaneura-staging-worker--sp-817-f7af4dcc-w`, digest
`sha256:9cb59027d4dcd523ba7a2f9d6273dee8707fe5cbabf07af20c72ad242a1c4846`.
These are separate current images. Retain both and current schema until a reviewed,
current-trunk candidate is explicitly selected. Never replace them with an older
Core image to test retention.

The actual staging model endpoint is `openaitestvhs.openai.azure.com`, with
deployment `gpt-4.1-2025-04-14` and model version `2025-04-14`; the infrastructure
directory's AI service name alone does not identify that configured endpoint.

The separate `tools/capture-retention` evaluation change contains the full fixtures,
semantic oracle, actual-model runner and disposable persistence test. It depends
on this runtime change; neither PR is qualified by secretless tests alone.

Recovery is a source revert of capture grounding and response-schema wiring followed by the
normal reviewed current-trunk release path; no database downgrade, deleted notes,
or restored old deployment image. Existing saved notes are never regenerated.
If model checks fail, keep the candidate unpromoted and preserve the failures.
GitHub Actions remains disabled. Relevant local checks and independent review
after the final substantive push are recorded in the PR. No deployment is part of
this flight plan's implementation.
