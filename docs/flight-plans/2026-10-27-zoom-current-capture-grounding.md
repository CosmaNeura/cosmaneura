# Ground the staging Zoom pilot with current capture rules

Tier 2: clinical note content; no migration, authorization expansion or deployment.
This candidate supersedes the generation approach in draft PR833, not its failed
evaluation results. Adapter10 R2 falsely called an answered medication question
unanswered; R3 rejected an empty unresolved-parts array. Live R7's legacy bot note
added a diagnostic differential and unstated Plan reasons.

Reuse current Core capture Plan-first selection, source-extractive Plan rendering,
neutral unidentified-source history, exact-field validation and medication-list
verification limitation. Keep the provider's actual schema, example and supported
preferences. Require explicit source reasons, never inferred reasons; qualify
missing reasons. Questions and replies are source context, without a separate
question classifier, missing-answer assertion or `source_review` response envelope.

Selection remains limited to the exact existing staging lab A/B identities, one
consumed-authorized Zoom meeting and durable successful Recall ingestion. The
queue hint does not authorize capture or bypass the worker's scope recheck.
The ordinary recording, admission, tenant, replay, publication and current-source
guards remain unchanged. Source recording boundaries cannot be selected as speech.

The pilot uses one configured model attempt with no SDK or task automatic retry.
The existing default queue is unchanged. No automatic second model checks or
repair calls are added. Broker redelivery remains subject to durable note replay
and finalization guards; a failed attempt requires deliberate operator retry.

Recovery: revert the pilot dispatch/adapter commit; new jobs use the old default
path. No existing transcript or note is rewritten. No production activation is
part of this work. No live staging deployment, database or model call is authorized
by this document. Model and owned-PostgreSQL evaluation require a separately
reviewed bounded operator. Test literal contracts and existing scope boundaries,
then independently review actual R2/R3/R7 outputs; valid references alone do not
prove selection completeness, clinical interpretation or usability.

## Retained-output repair

The first candidate (`c86e13e4`) passed 162 PostgreSQL tests but failed both
finite model quality reviews. R2 paraphrased required negative statements and
retained a note-editing preference. R3 omitted the partial fall/other-injury
question and selected a past medication fact as Plan. The raw outputs remain
unchanged in the synthetic regression fixture; the same rejection test fails
against c86 and passes against this repair.

The bot requires complete original negative statements in permitted existing
history fields. Standalone writing directions in
generated history fail validation. Clear English past-tense-only statements and
standalone editing requests are excluded from Plan's source enum and separately
checked by the resolver. A complete mixed entry with a prospective clause is
retained intact, including qualifications; the server never slices stored text.

These lexical checks are conservative failure conditions for this English pilot,
not clinical classification or proof of complete source selection. They can
require more review or reject an otherwise useful draft. Schemas without a
permitted history destination do not gain invented fields. All native source
and publication guards, zero automatic retry boundaries, and finite semantic
review requirements remain in force. A one-participant recording carrying two
voices is not evidence of acoustic diarization; exact source quotation can still
produce an unwieldy grouped-source note. No audio or overall quality acceptance
is implied by this repair.

## Compact source-context correction

R8 passed 180 PostgreSQL tests but rejected both actual model responses. The R2
rejection reflected an overstrict requirement to copy every question, including
fully answered questions whose clinical facts were present. R3 still omitted the
actual partial fall/other-injury question. Keep both outcomes; do not relabel their
original rejected outputs as successful model evidence.

Known-role bot drafts now select a small transient `source_context_refs` list for
clinically necessary partial-reply or conflicting context, rendered exactly in an
existing Objective/history field. No question/answer pairing, answer-status claim,
new persisted field or automatic extra call is introduced. Fully answered questions
need not be copied. Unidentified speech retains its existing source-history
selection. Valid references do not prove clinical completeness; the actual R3
partial-question review remains a separate required semantic gate.

Actual R7 maps one provider participant to one 392-word transcript entry. For such
coarse turns only, the note prompt uses exact punctuation-delimited text passages
with original entry/character offsets. Existing source text, source speaker ID,
recording times and persisted authority are unchanged. Passages do not create
acoustic speakers or verified clinical roles. Adjacent corrections/qualifiers are
kept together conservatively and full context remains visible in the prompt;
cross-passage interpretation and omitted qualifications still require review.
The provider ASR numerals are preserved as received, without fixture-truth repair.
This is a separate R7 input-shape requirement, pending canonical stored-source and
actual schema confirmation, bounded model selection, and independent review.

R10 passed 199 PostgreSQL tests but actual R2 repeated literal negative quotations
in Objective alongside Subjective paraphrases, and R3 still omitted the partial
fall/other-injury question. Both remain failed quality evidence. Required literal
negatives now have one existing destination: Subjective only when every required
source is Patient-labeled, otherwise neutral Objective/history. Their words must
occur in that field and cannot be duplicated in another history field. Unknown
roles never gain patient attribution through this rule. A general cough/wheeze
example clarifies multipart reply scope without becoming encounter evidence or
adding an answer-status assertion. Selection completeness still requires actual
model review. The canonical R7 read confirmed one legacy Doctor-labeled entry
with text/times exactly equal to Recall mapping; a prospective guarded input
retains those words/times while qualifying that unverified label. The saved row
is unchanged, and the observed current schema/example/default preferences are
the R7 evaluation inputs.

R11's first real-shape R7 selection put “We are not starting a new medicine”
in Plan correctly, but the literal history gate rejected its absence from
Objective. A narrow placement exception now applies only after exact Plan
references, source boundaries and past-only rejection have passed, and only
when every negative clause in that source is prospective. Historical medication
facts and mixed symptom denials cannot use it. Original rejected outputs stay
retained; diagnostic rerendering does not count as a new model result. Known
Patient negatives also retain their requirement in HPI, Chief complaint and
other custom history fields; unknown roles gain no patient-only alias target.
R2's faithful paraphrases still fail the literal wording contract, and R3 still
omits partial-question context. Neither limitation is declared resolved here.

The successor removes generic future-cue waivers: every negative occurrence must
have its own local prospective care cue before an already validated Plan can
waive history duplication. A fever denial before a semicolon or after “because”
stays required even when the same entry contains a future decision. The source
text is unchanged. Generic English number words only identify a future interval
correction; no fixture indexes, symptom truths or corrected values enter runtime.

Primary history for Patient-labeled required facts is now source-selected, using
the current patient-only/both-speakers preference and existing field destination.
Required negatives are retained from their exact sources once, plus selected
positive history/correction context; competing generated prose is rejected.
Every transcript question receives an explicit structured selection row. Rows
contain references only, never answer status or inferred missing-answer text.
Selected questions/replies are deduplicated against existing source history.
Empty selections still require independent clinical completeness review; format
validity cannot prove that a partial question received sufficient context.
Patient-only Subjective stays empty when no source has a Patient label, including
internal labeling/preference explanations. This does not change persisted roles,
transcripts, user schemas, examples, saved preferences, authority or retry policy.

R13's three real configured-model requests rendered and its 232 PostgreSQL tests
passed, but independent review still failed note quality: optional nonclinical
source selection, repeated questions/recaps and long notes. Those raw responses
remain unchanged. The next deterministic rendering step groups adjacent source
attribution only when both the original source label and any provider speaker ID
match. It deduplicates references already rendered in Plan, preserves mandatory
historical negatives even in mixed Plan sources, and merges repeated context
headings. Equal text from different source references is not identity evidence.
Patient-only empty-field enforcement also covers the same HPI/Chief complaint/
History of present illness aliases used for negative placement, including source
selection channels. Three existing SOAP requests remain subject to exact request
comparison before any retained-response rerender; that is not a new model result.
Nonclinical selections and semantically repeated recaps remain separate unresolved
quality findings and cannot be fixed by deleting fixture-specific words.

The optional-history successor separates automatic required negative sources
from optional source-kind choices. Every permitted optional entry is classified
as clinical history, care plan, nonclinical context or redundant recap; only the
clinical-history selection renders in the primary field. This classification is
not independently verified clinical truth. Required negatives cannot be removed
by a kind label. Model selection must still retain every clinically necessary
positive fact, correction and qualifier. Paired generic partial-reply and fully
represented-reply examples guide neutral question selection without rendering
answer-status assertions or inventing a missing answer.

A narrow "without" placement exception requires an explicit prospective plan,
a numeric duration and an activity restriction, plus prior valid Plan selection.
Every negative occurrence must still pass its own local check; symptom absences,
past periods and mixed historical denials remain required history. The grammar
uses general activity/duration terms, never encounter identifiers or marker words.

For combined history/prospective custom headings, the source-extractive prospective
channel takes precedence. They do not also enter primary/neutral history or the
empty-history set. Their request/schema and rendered "Prospective source context"
label state that precedence; competing free prose rejects, while ordinary HPI and
Chief complaint remain excluded for unknown-role patient-only speech. This is a
channel contract, not proof that every model-selected source is a clinical plan.
Fresh bounded three-case model evidence and independent final review are pending.

R15's three HTTP-200 responses remain failed quality evidence: optional markers
and pause logistics were removed, but R3 again omitted the complete fall/other-
injury question. The server now conservatively retains complete questions with
structural "and"/"or" wording as neutral source context. This can also retain fully
represented replies' questions and conjunctions joining one predicate; it never
asserts two findings, a missing answer or a clinical answer relationship. Pure
source-writing instructions do not become mandatory clinical questions.

Valid model reference rows remain unchanged. Required question refs are added
server-side with existing selected following context. If none exists before the
next question, the next actual non-directive source entry is retained as adjacent
context, not a verified reply. Existing reply/history selections remain intact.
Source identity deduplication prevents repeated text from the same reference. An
existing unidentified-history field combines its validated model and server refs
chronologically and revalidates the union; no new field or inferred role is added.
Single-scope questions keep optional behavior. This bounded compactness cost is
intentional. Existing R15 requests must remain exactly equal during offline raw-
response replay, with added server refs recorded separately from model selections.
No new provider call or regrading of original failed responses is implied.
