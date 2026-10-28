# Capture Objective source grounding

Risk tier: **2** (clinical note generation). This changes newly generated capture
drafts only; no schema, data migration, dependency, provider, configuration or
deployment change is included.

An actual retained known-role SOAP draft rendered a home blood-pressure statement
as `118/72 mmHg`, although the source did not state a unit. Other repetitions put
that reported measurement in Subjective and asserted that Objective was not
mentioned. A patient-reported measurement can be retained as reported diagnostic
data with its original attribution; it does not establish an examination by the
note writer.

Eligible Objective fields now use the existing source-reference contract for all
recorded roles. The server emits only complete selected source entries in original
order and with the original role. Known-role histories outside Objective retain
the existing narrative path. Patient-only Subjective continues to exclude
unidentified speech. Combined Assessment, signature and other excluded headings
are not repurposed. Empty selection displays the existing review limitation; it
does not assert source absence or normal findings. The schema's grounded prose
channels are empty, leaving reference selection as the requested output.
The newly grounded Objective rejects a competing nonempty prose value even in a
JSON envelope with valid source references and without provider schema enforcement.

An exact complete Patient statement repeated as a standalone line in patient-only
Subjective is removed only when that unique source entry is already retained in
Objective. Paraphrases, mixed lines, multiline entries, other/unidentified roles
and text appearing in multiple sources remain untouched. Prospective headings
use the same classification as PlanGrounding and are never changed by history
deduplication, so nonempty prospective prose remains subject to its original
rejection. Exact-line matching does not determine whether neighboring narrative
text qualifies a matched line; that paragraph-context limitation requires review.

Objective headings containing medication/history terms use the same original-role
contract for current-list, change-history and source-context categories as ordinary
Objective reference lists. The categories remain unverified source context rather
than proof of a complete medication list or treatment decision.

This is a narrow factuality boundary. It does not normalize units or numbers,
infer roles, classify clinical findings deterministically, remove source markers,
or slice entries to discard trailing qualifications. The prompt asks the model to
select reported measurements and examination statements for known-role Objective
while retaining other history in the appropriate narrative field. Prospective
Plan selection and complete-source rendering remain intact.

## Evidence and remaining gates

Three actual configured-model calls on the first Objective candidate returned
empty Subjective and Assessment strings, omitted clinical history and explicit
negatives, and omitted a separately stated prospective decision. Their original
raw responses and failing grades are retained. After all three repeated that
failure, the remaining fourteen calls were stopped before provider execution.

The history prompt now names the exact existing reference-only fields and scopes
its empty-string, selection and omission instructions to those fields. Remaining
existing narrative fields retain their original source preferences and supported
history. Subjective and Assessment instructions apply only when those headings
exist. No new sections are required or added. History recap omissions cannot
remove separately stated prospective care decisions or commitments. Beyond the
prior candidate, this changes only the history prompt, not its resolver, the
response schema, PlanGrounding, preferences or persistence checkpoints.

The affected integration fixtures now supply the required empty grounded prose
channel and valid history envelope before testing original prospective guards.
Their original Plan source/negation/condition/refusal and invalid-reference
assertions remain. Known-role and non-patient-only cases check the active Objective
contract, while schemas without Objective preserve the original raw-byte bypass.

The original seventeen request, source and decoded response bindings and their
original oracle failures remain immutable. The copied synthetic regression raw
response includes its original hashes and is not a new provider receipt.
Adversarial inputs with an added reference envelope prove only source resolution,
never clinical selection or a fresh actual-model pass.

Meaningful tests fail on the previous source and pass on this source for the
observed unsupported unit channel, known/mixed-role attribution, complete
corrections, uncertainty, unavailable Objective selection and excluded fields.
Existing source bounds, duplicate-reference rejection, saved-note early returns,
authorization and finalization checkpoints remain required integration checks.
Final source pins and execution results belong in the independent review packet.

Actual model qualification on this final source, independent semantic review,
final integration with the selected deployed schema/preferences source and live
capture/save/reopen/usability acceptance remain required. Retained mixed editorial
entries, duplicated commitments and source quotations are not claimed fixed by
this narrow change. No full clinical or usability acceptance is inferred from an
offline replay or an empty prose schema.

Five actual calls on the scoped-history candidate retained the clinical facts.
The three known-role cases and the first unidentified-role case passed their
clinical and Plan gates. The next unidentified-role case selected an editorial
summary request with retrospective medication history into Plan, although that
entry contained no future care decision. It also retained a narrow denial while
omitting the complete multipart clinical question. The remaining twelve cases
were stopped before provider execution. All five raw responses, source bindings
and original grades remain retained; the prior candidate's 276 PostgreSQL tests
passed without skips, with its owned schema dropped afterward.

The successor changes only the history prompt. Prospective eligibility now
precedes the history omission exception: only an explicit future care decision or
implementation qualifies, including care commitments, negations and conditions.
Writing directives, recording logistics and past/current medication behavior do
not qualify alone. A genuine mixed entry containing a separate future care
decision retains its complete words and qualifications. This neither clips source
entries nor deterministically classifies their content in the server.

Complete relevant clinical questions and information requests are selected source
context independently of whether replies were recorded. A narrow reply does not
justify discarding or trimming the original multipart request or expanding its
denial. The renderer still uses an unpaired chronological source union and asserts
neither an answer relationship nor an answered/unanswered state. New controls
check these explicit prompt duties and protect genuine mixed care decisions and
patient commitments; they do not establish a fresh actual-model success. The
original failed selection and partial-context gap remain failed evidence.

## Separate selection tasks after the reproduced joint-task failure

The prompt-only successor repeated the same editorial Plan selection and omitted
clinical question. Two isolated actual calls using its unchanged system prompt
selected the required Plan sources without the editorial request and retained the
complete clinical question plus its narrow recorded reply. These diagnostic
outputs were source selections, not saved clinical notes. The history finite
check still reported `no-positive-fall` because its literal pattern matched words
inside the required quoted question; that failed result is retained and requires
independent semantic review rather than being regraded.

The configured capture path now selects Plan and reported history in separate
one-shot tasks, then generates only unconstrained original narrative fields when
they exist. A schema containing only source-grounded fields needs no narrative
call. Every task uses the complete original sources, schema and saved preferences;
one task's generated output is never clinical input to another. The original
resolvers validate selections before further calls and validate the original
combined envelope before finalization. Shared Zoom system instructions, role
labels, source bounds, persistence and source/authorization checkpoints remain.
The existing source/auth checkpoint runs before each new provider request and
closes its database transaction before that request begins.
The complete model phase retains its 35-second deadline and SDK retries remain
zero; cancellation cannot admit another stage or a late publication. This may
increase tokens and call count, which must be measured in the actual composed
normal recording before qualification. Focused controls pass, but actual composed
note quality, independent review and live Save/reopen remain required.

## Recovery and review

Revert this change to restore the previous behavior for future generated drafts.
Existing saved notes and deliberate clinician edits are not regenerated. No data
rollback or migration is required. Independent agent review must pin the final
substantive source before publication. Publication, merge and deployment require
the operator selected by the current user authorization.
