# Capture note retention candidate

Current status: the final neutral-context qualification matrix described in the
last section failed. Earlier sections record historical candidates and criteria;
none of their local checks establishes current promotion approval.

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

## October 26: one substantive prospective output channel

Tier 2 local candidate from `97614048041a0de6b50cd43dea6502d956a26f03`.
A retained synthetic comparison showed the raw free-text Plan included diary
frequency/duration detail while its source-reference array omitted the relevant
entry. The resolver correctly discarded untrusted prose, so the saved Plan lost
that detail although the same entry remained in Objective. The simpler selection
prompt candidate failed this check and remains separate, unpromoted evidence.

For new capture generation, prospective strings in the transient model envelope
must now be exactly empty. Source-reference arrays are their only substantive
output channel, enforced by both configured schema and resolver. Custom persisted
field names, full-entry extraction, source order/attribution, one configured call,
history behavior and ordinary summarization are unchanged. No excerpts or model
paraphrases are substituted. Existing saved drafts/replay return before generation.

Local supplied-reference tests prove rejection/extraction, not model completeness.
Independent source review, configured-model comparison and a fresh capture remain
required before qualification. Preserve all failed outputs and the manual clinical
gate; no saved visit is regenerated. Recovery is a reviewed current-trunk revert of
this transient contract change; no schema/data migration or old-image rollback.


### Implementation-detail selection candidate — local, not promoted

Actual native-source comparison04 kept the empty-only prospective channel but
still selected the diary implementation entry only as reported history. Both
R2 arms omitted its timing and duration from Plan; the broader no-change
decision was retained. R3 candidate returned SDK HTTP429 with no note, so that
comparison is unavailable. Original responses/oracles remain unchanged.

This stacked Tier2 candidate changes only the prospective selection prompt. It
defines explicit future care commitments and distributed implementation details
as Plan content, while excluding bare acknowledgments, recording logistics and
editing requests. Necessary mixed entries remain complete attributed source;
no excerpt, inferred advice, history-ref promotion or additional model call is
introduced. The earlier selection-only paragraph failed without the empty-Plan
contract; the combined approach remains an unproven interaction.

Supplied-index tests protect full qualifiers, cancellations and explicit
history/Plan overlap; they cannot prove model selection. A separately reviewed
paced evaluator must compare actual baseline/candidate outputs, preserve full
narrative review and raw failures, and complete the planned repetition/edge
matrix before acceptance. No current notes are regenerated. Recovery is a
current-trunk reviewed source change; saved notes/edits and ingestion/replay
remain unchanged. No deployment or promotion is authorized by this candidate.

### Selected question context — separate local contract candidate

The paced comparison05 retained the missing R2 implementation and no-change
entries in the candidate Plan in one sample, while both R3 arms still lost the
unanswered component of a multipart injury question. The history prompt expressly
instructed omission of unanswered questions, and its source renderer replaced
the model's narrative. The Plan improvement is narrower than clinical acceptance;
the original outputs, finite passes and manual failures remain unchanged.

This Tier2 delta is stacked on the reviewed Plan implementation candidate. It
changes only the Unknown-role, patient-only history selection envelope and
renderer. Each existing history field has factual selections and a bounded list
of complete question references with selected later speech references. The prompt
requires reading all later answers and excludes fully answered questions and
nonclinical logistics. The server checks source identity, chronology and bounds,
then renders each selected entry once in source order under neutral context labels.
It never asserts that an answer is absent, invents an answer, or changes a speaker's
role. Valid references do not prove the model's question classification or semantic
completeness. Deliberately supplied wrong selections can still be unhelpful.

Empty question selections preserve the prior rendering, including medication-list
uncertainty and review-required empty history. The persisted clinical fields,
templates/preferences, single configured call, Plan extraction, capture authority,
ingestion and saved-note replay are unchanged. There are no excerpts, fixed case
indexes, source instructions promoted to authority, data migrations or old-note
regenerations. Known-role and non-patient-only paths do not use this contract.

Pure tests cover complete partial questions, later answers/corrections, invalid
or non-speech references, bounds and exact prior output. A private reconstruction
uses retained synthetic R3 source only to verify rendering; it is not a new model
or capture result. Actual-PG save/replay and invalid-selection rejection tests are
prepared separately and require selected execution. Independent source review,
actual paced model comparison, the repetition/edge matrix and a fresh capture
remain necessary before promotion. Recovery is a reviewed current-trunk source
change; retain existing notes and all earlier failures.

### Clinical request selection — local prompt correction

Actual comparison06 used that question envelope and returned an empty question
selection for both candidate cases. The R3 candidate retained the no-fall answer
but omitted the complete fall/other-injury question, so its Objective was byte
identical to baseline and still incomplete. The complete raw response, prompt,
saved fields and original finite pass/manual failure are preserved. The first
observed loss is model selection; no parser, renderer or persistence failure was
demonstrated, and the reason for the model's omission is not established.

This Tier2 correction changes only the history selection prompt on `cbb84a1`.
Select clinically relevant information requests with their real later responses
as neutral source context, without first deciding whether an answer is missing.
Complete, partial and absent responses all preserve what was actually requested
and subsequently stated. Recording logistics, template instructions, rhetorical
editing and other nonclinical conversation remain excluded. No model or server
may infer a denial of an unanswered component from a partial response.

Including fully answered clinical requests can increase note length. The existing
bounded context list and source-order deduplication still apply; this is not a
whole-transcript fallback. Classification, pairing and completeness remain actual
model and clinical-review questions. The renderer does not claim that a selected
question is unanswered, or that valid references establish clinical adequacy.

The response schema, parser, renderer, Plan implementation, preferences, one-call
shape, authority and saved-note replay are unchanged. A retained synthetic actual06
fixture verifies that its original empty selection still reproduces the failed
saved fields exactly. A separately labelled supplied-reference reconstruction
proves only literal rendering with the real partial response, not a new model pass.
There are no fixture references or clinical literals in product logic.

Independent source review precedes a new paired evaluation using the complete
actual06 candidate history module as baseline and this prompt as candidate. Run
only the reviewed first four calls after the corresponding persistence gate; do
not start repetitions or the edge matrix while that comparison fails clinical
review. Preserve all earlier outputs and require a fresh capture before promotion.
No deployment, source promotion or saved-visit regeneration is part of this local
correction. Recovery remains a reviewed current-trunk source change.

### Request classification and forward references — local prompt correction

Actual comparison07 selected the previously missing complete multipart question
and its real partial response. The same candidate also classified a declarative
negative report as a question and referenced that entry as its own response.
The existing strict history parser rejected this self-reference, and the actual
fresh persistence read confirmed zero candidate notes. The other three outputs,
raw candidate response, original oracle results and cleanup remain preserved.
This is not a clinical pass or evidence that the whole candidate note was usable.

This Tier2 prompt-only follow-up on `c10deda` makes two existing constraints
explicit: declarative reports, answers, denials and recaps are factual selections,
not information requests; each selected response index must be strictly greater
than its question index. An empty response array remains valid and must not be
filled with the question itself. Genuine requests phrased without a question mark,
real later answers and neutral complete/partial context remain supported.

The response schema and rejecting parser stay unchanged. There is no dropping,
repairing or accepting invalid model output, automatic retry, second call or
source-specific rule. Structural rejection is already enforced server-side;
encoding the cross-property relation in a new per-source response schema would
broaden this correction without establishing semantic question classification.
The actual07 fixture must still be rejected under this candidate. Its retained
declarative-source misclassification is separately recorded; a valid reference
alone still cannot prove that the source is a question.

Focused offline tests cover the exact rejected response, the genuine request
and partial response already present in it, later/self/earlier reference behavior,
and unchanged rendering contracts. These do not show that a new model response
will classify or reference correctly. Independent source review and separately
selected actual persistence/model comparison are required before more matrix
work. No source promotion, deployment or saved-visit regeneration is authorized;
retain the first sandbox PG failure, its successful selected retry and all model
outputs. Recovery remains a reviewed current-trunk source change.

### Neutral source context after actual paired-selection failures (2026-10-26)

Tier 2 candidate, not a clinical acceptance or deployed change. Actual evaluator08
used the real configured model with strict JSON Schema, but integer-only paired
question/response properties permitted self-pairs. Both R3 responses were rejected
before a note was saved; the R2 candidate also labeled an editorial request as a
clinical question. The immutable raw outputs and root independent review remain
under `integration-20261025/reviews/capture-request-actual08-root-independent-01`
and `capture-context-actual08-durable-01`. No old result is regraded as a pass.

This separate candidate removes question/response pairing from the transient
response contract. Eligible ordinary history fields select one `source_refs`
list containing relevant complete clinical context. The server renders source
words in chronological order with the existing source attribution and a neutral
heading, without claiming that a selected entry is a question, an answer, a
finding, or evidence of a missing answer. No invalid response is repaired or
translated from the old envelope. Relevance and complete selection remain model
and clinical-review responsibilities; an editorial selection is still a failure
of usefulness even though it no longer receives a false question label.

Medication fields retain `current_list` and `change_history`; `source_context`
holds additional literal context. Each list rejects duplicates, foreign indexes
and unrecognized source types. Existing disjoint current/change validation stays
intact; a valid source also selected as context is rendered once. The current-list
verification caveat, typed scratch attribution, empty review limitation, known-role
and non-patient-only paths, prospective Plan source extraction, saved field names,
preferences and one configured call remain unchanged. Existing notes/replays bypass
new generation and are not rewritten. No database migration or schema row change.

Pair-specific `anyOf` with suffix enums was considered: 45 source rows require
1,035 enum entries before other schema fields; current OpenAI documentation caps
aggregate enums at 1,000. Numeric-bound alternatives need Azure acceptance and
still require the failed semantic pairing task. The neutral-list schema has no
pair expansion. It does not guarantee unique/range-valid model output; the server
continues to reject invalid selections. This is not an Azure scalability claim.

Before evaluation: independent final-source review, actual disposable PostgreSQL
positive/replay and malformed-envelope cases with exact baseline/candidate modules,
then a separately selected four-call comparison with retained raw/finite/manual
results and 65-second spacing. All Plan details, partial multipart question plus
real response, later corrections, role uncertainty, medication history and source
instruction boundaries require independent review. Three repetitions and the full
eight-edge matrix remain required after the first clinical comparison passes.
Stop on failure; no extra call, automatic retry, source promotion or regeneration
of saved visits. Recovery is to leave this candidate unactivated and retain the
previous deployed implementation and all evidence; this is not data rollback.

### Standalone source instructions: failed qualification and local repair

The frozen neutral candidate `90bf9b6` was compared with the exact deployed
`9761404` capture implementation in three paired R2/R3 repetitions, then the
original eight bounded retention cases. All 20 configured calls saved/reopened
once with zero replay calls and settled disposable-schema cleanup. Candidate
Plan inclusion passed throughout; original finite checks passed 18/20 and the
predeclared neutral-header calibration passed 19/20. RET07's raw person-history
failure was a header-recognition limitation: both people's complete statements
were retained separately. RET08 failed both versions because its selected history
included a standalone instruction to the note generator. It was copied literally,
not asserted as a performed normal examination outside the quotation. This is
still a blocking exclusion failure, and the full matrix remains FAILED.

The durable raw outputs, checks, author and independent reviews are retained under
`integration-20261025/reviews/capture-final-qualification-*`. No earlier stricter
question-inclusion experiment was regraded. Original RET05 prohibits invented
answers; copying every unanswered question is a separate optional measure.
Repetitive source labels, mixed editorial entries and an occasional blank
Assessment remain presentation limitations. The 20-call matrix covers the
observed four-field patient-only template, not every custom or known-role request.

This new Tier 2 local candidate integrates the unchanged neutral implementation
onto current trunk `1715c1c33108da0e2335049db6b00f69afc7bb81`, whose intervening
Zoom-status changes do not overlap the note modules. The only additional runtime
change is the enabled history-selection prompt: omitting an instruction means
not selecting its index even for quotation. Instructions to a model, assistant,
note writer or software about its response are distinguished from real clinical
reports, clinical information requests and care instructions. Medical words in
a writing directive are insufficient. A mixed entry needs separate clinical
content worth preserving, then its complete wording and qualifiers remain intact.

There is no substring filter, automatic correction, second model call, new role,
source-specific index, or oracle relaxation. The actual failed response must
still reproduce the leaked instruction and fail the original finite check in
offline regression. That test protects the evidence and renderer; it does not
prove the new prompt will select correctly. Plan, schema, parser, rendering,
authority, default summarization, preferences and replay are unchanged from the
frozen neutral candidate. Disabled history paths receive no added instruction.

Independent source review and separately selected actual persistence/model
evaluation are required before any promotion decision. No further provider call
or staging release is part of this local repair. Existing saved notes are never
regenerated. Recovery is a reviewed forward source revert on then-current trunk,
preserving schema, notes and unrelated Zoom changes, never an old-image rollback.
