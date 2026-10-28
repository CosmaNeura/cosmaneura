# Capture note retention

Current candidate: runtime `8793cd2d59f51fcd6f8d6b0fd67918ad6727f52d` on
trunk `1715c1c33108da0e2335049db6b00f69afc7bb81`. Its fixed-profile synthetic
retention qualification is independently accepted; it is not deployed. The final
implementation, evidence limits and promotion requirements are below. Earlier
failed candidates and their unchanged outputs remain in the private lab record.

## Inherited implementation history

The following pre-existing sections describe the original grounding and SOAP
medication correction. They are historical context, not new release authority.

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

When every transcript role is unidentified and Subjective is patient-only,
recognized existing history/Objective/ROS/medication fields use source indexes in
the same model response. A capture-local resolver stores the selected complete
source text and its actual attribution, replacing generated prose in those fields.
It does not add note fields, infer roles, or copy the whole transcript by default.
Current medication reports and retrospective change history have separate selections,
but those model categories never establish a verified current patient list. Medication
fields retain explicit list uncertainty and render the union of selected statements
in source order under a neutral heading, never asserting that no medicine is taken.
Typed scratch notes keep their distinct source label. Empty history selection
requires review. Known-role captures and other Subjective preferences retain the
existing path. The prospective PlanGrounding implementation is unchanged.

This boundary prevents rewritten direct quotations and manufactured roles in the
grounded fields. It does not prove that all relevant entries were selected or
classified correctly. Questions, unresolved conflicts, corrections and source
instructions remain adversarial semantic cases in the actual-model evaluation.

Validation requires the complete observed synthetic ASR transcripts (42 and 45
passages), three baseline/candidate repetitions each, the eight bounded semantic
cases, known-role and unknown-role checks, and the actual response envelope and
PlanGrounding resolution. The finite oracle must reject archived faulty drafts
and adversarial number, time, negation, and attribution outputs. Independent
content review is required in addition to oracle results. All outputs are retained.

Initial source base: `f7af4dccd72a213b003283e8b19bebbaedf23e69` (Session Prep PR 817).
Initial read-only October 25 staging observation: web revision
`cosmaneura-staging-app--sp-817-f7af4dcc-web`, image digest
`sha256:07274f5e98f7a143050e36e71c1250f4cea3e4cd3662147f499ff279cea00e6d`;
worker revision `cosmaneura-staging-worker--sp-817-f7af4dcc-w`, digest
`sha256:9cb59027d4dcd523ba7a2f9d6273dee8707fe5cbabf07af20c72ad242a1c4846`.
These are historical evaluation images, not a current deployment declaration.
Reconcile the then-current web/worker images, configuration and schema before each
release. Never replace them with an older Core image to test retention.

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

## October 26: SOAP medication-list uncertainty

Tier 2 follow-up from `e1037b18bc425d269245c56dcb681c0f5798edf9`. An actual
synthetic native capture's original retained SOAP draft preserved medication-change
history but omitted explicit current-list uncertainty. The exact original body was
recovered by its earlier prepare hash after unintended UI edits; the raw model
response was not retained. This establishes the saved-output omission, not what
the model wrote before resolution. The generic Objective resolver replaces model
prose, while the existing fixed list limitation was added only to medication fields.

For the already-enabled unidentified-role, patient-only history path, a template
without a medication field now receives that same fixed limitation once in its
first eligible Objective field. Empty selections still require review. Source
words, attribution, field order and all other fields stay unchanged. Dedicated
medication-field output, known-role paths, other Subjective preferences, prompt,
response schema and one-call shape are unchanged. No source text is searched to
infer a medication list, and no marker or mixed-content source entry is filtered.

Validate the SOAP omission regression, empty/named/temporal selections, first-field
placement, and unchanged dedicated-medication/disabled paths with the existing
grounding suite. A separate offline reconstruction of the actual selected entries
can verify the renderer delta; it is not the original model response or a fresh
native/save/dashboard acceptance run. Preserve the failed original and every edit.

Before any release, obtain independent review of the final pushed head and fresh
current-trunk deployment reconciliation. The observed baseline was web69 at
`e1037b18`, worker40 at `63094f2d`, schema `9bcaa341475f`; re-observe all pins rather
than treating these as perpetual authority. Preserve worker/scheduler, Session Prep,
configuration, schema and existing notes. Recovery is a separately reviewed forward
source revert of this small renderer delta, never old-image rollback or regeneration.
A fresh native capture and normal save/reopen/dashboard content comparison remain
required after a reviewed staging release. No paid model call or live regeneration
is part of this correction.

## Qualified capture contract and release preparation (2026-10-26)

Tier 2, one deployable outcome: retain source-backed clinical context and complete
prospective care details in newly generated capture notes. Three runtime modules
change; the other changed files are synthetic regressions and this flight plan.
There is no database/template migration, new dependency, authority or ingest
change. Ordinary summarization and existing saved-note replay remain unchanged.

Prospective fields now have one substantive output channel: source references.
The transient schema and parser reject nonempty free-text Plan strings. Selection
includes explicit future care commitments and their distributed timing, duration,
conditions and corrections. Final Plan still contains complete attributed source
entries; no excerpt, generated paraphrase or inferred advice is substituted.

For the existing all-unidentified-role, patient-only history path, ordinary fields
select neutral source-context indexes, without question/answer pairing or a claim
that an answer is missing. Medication fields preserve separate current/change
selections plus neutral context, original attribution and current-list uncertainty.
The prompt excludes standalone instructions to the model or note writer even
when quoting them, while allowing independently meaningful clinical content in
mixed entries. The server rejects invalid references; it does not repair or
filter a model response. Known-role history paths and other Subjective preferences
retain their prior behavior. Selection completeness remains model-dependent.

The actual configured model was called once per fresh case, with retries zero,
unchanged request options, and at least 65 seconds between SDK completions and
subsequent calls. Qualification Q03 contains 14 fresh candidate cases: three
R2/R3 repetitions and eight original retention cases. Six complete matching
`9761404` baseline requests/results are explicitly reused archival comparisons;
there were no fresh baseline calls in Q03. All 14 candidate V2 retention and Plan
checks passed, with save/reopen and zero replay calls. Five batch cleanups settled.
The actual PostgreSQL gate passed ten positive, four malformed-envelope and two
context cases. Source checks passed 160 offline tests; evaluator controls passed
205. Raw responses and every final field received independent semantic review.

Durable private evidence: `integration-20261025/reviews/capture-final-qualification-matrix-03`.
Matrix receipt SHA256 `537a22415039be3b1e4fa4c6ace62c2ff0a91d76483350d86ea4d350b5cfb00f`;
independent acceptance `d95d42186be93d5e512c3f9ec22cabd8208dcf1b5e130996fa5cb03c7d06915c`.
The earlier Q02 20-call matrix remains FAILED for literal source-instruction
leakage. Q03 RET08 excludes that instruction while retaining its source transcript.
RET07's original raw flag remains FAIL; its accepted, predeclared neutral-header
calibration is independently corroborated by both people's exact source text.
R3's optional full-question copying metric remains FAIL in all three repetitions,
without an invented answer or denial under the original RET05 gate. No stricter
prior experiment is silently regraded.

Qualification is limited to the observed four-field SOAP, unidentified-role,
patient-only profile, effective example None and Plan-reason preference false.
Repeated source labels and mixed editorial wording remain presentation limits.
This does not qualify arbitrary templates, long sessions, acoustic identities,
a fresh native UI journey, or the full system. It adds no model retry or call.

Before release: obtain independent review of the final substantive pushed head,
reconcile exact current trunk/image/configuration/schema and preserve unrelated
Zoom changes, workers and scheduler. The last reconciled web72/image80f1/source976
is baseline evidence, not perpetual deployment authorization. Build and verify
an exact merged candidate through the separately reviewed release path; no
staging mutation or production promotion is part of this source PR. Require a
fresh synthetic native capture and normal save/reopen/content comparison after
deployment before claiming that journey passes. GitHub Actions stays disabled.
Recovery is a separately reviewed forward source revert on current trunk, with
no old-image rollback, database downgrade, deleted notes or regeneration of saved
visits. Preserve all qualification and prior failure artifacts.
