# Zoom bot source fidelity pilot

Tier 2: patient-note path. No schema, migration, dependency, authentication or
Session Prep change. This candidate is not deployed or clinically qualified.

## Problem and scope

The actual configured model's synthetic named-participant R2/R3 baseline used the
normal A/B four-field SOAP templates, extracted examples and default preferences.
R2 lost an explicit fever denial. R3 asserted an unsupported exclusion of fracture,
dislocation and neurological compromise. Action extraction introduced booking and
correction tasks; the note prompt then demanded every derived action. Default
instructions also required an inferred reason and excluded ROS negatives from
Subjective even when no ROS field existed. A final appended paragraph did not
reliably correct these conflicts; those outputs remain failed evidence.

The two-call prompt correction was also rejected after four configured calls:
Plan reasons were invented and R3 still generalized an unanswered injury question
and asserted an unsupported neurological/systemic exclusion. All 26 outputs remain
failed or diagnostic evidence; the finite oracle missed that broad exclusion.

Plan-only grounding was also rejected: two further configured calls still asserted
an unsupported serious-pathology exclusion and extended an unanswered injury
question to a denial. All 28 prior calls remain preserved; no failed candidate was
deployed or silently repaired.

The selected pilot now makes one index-only call for every existing schema field.
The server renders complete selected source statements with printed source numbers,
model-classified or unidentified attribution and a placement-review notice. Empty
fields remain visibly review-needed. No model-written clinical string is accepted
for any field. The conventional narrative composer and capture code are unchanged.

This is a source-referenced draft, not polished SOAP. Assessment offers source
statements to review, not new inferred diagnoses or clinical reasoning. Schema,
example and customization rows stay unchanged, but source order and whole blocks
limit grouping, pronoun/style rewriting and reason preferences. A complete source
block may mix topics or include a question; it is never shortened into a finding.

Word preservation is not clinical correctness: selection may omit facts, lose a
correction's context, choose the wrong section or imply incorrect person grouping.
These remain independent semantic/usability gates. The separate offline selection
oracle checks pinned R2/R3 omissions and correction context, without feeding any
fixture truth into runtime. No unknown custom heading escapes source grounding.

Only Recall's durable transcript dispatch adds the explicit versioned policy hint.
Both dispatch and execution require staging, the exact existing synthetic A/B
Doctor/Clerk/email tuple, one consumed Zoom appointment link, and successful Recall
ingestion of that exact transcript. The hint does not grant record access. Existing
access and capture-ownership guards still run. Missing/foreign/ambiguous scope uses
the unchanged default path. Ordinary callers and Scribe capture do not set the hint.

## Evidence and acceptance

- The preserved 22 prior configured-model calls are diagnostic evidence, not a
  live Recall recording or candidate acceptance.
- Sixteen structured default/capture-style prompt byte comparisons and two action
  prompt comparisons bind the pre-change `624f0c98179405386e97a663013347f8194cdd9f`
  implementation. Tests exercise unchanged queue arguments and default preferences.
- Actual isolated PostgreSQL tests select A/B and reject wrong identity, deployment,
  unconsumed/ambiguous links and absent/foreign ingestion; no live membership writes.
- The finite oracle retains original failed outputs and adversarial regressions.
  It does not prove global clinical, citation, role or action correctness. SOAP's
  absent medication-list field removes only that field-specific unknown-list check;
  retrospective medication history and false empty-list claims remain checked.
- Before any merge: independent review of final pushed bytes and actual candidate
  outputs. The next bounded evaluation is exactly one final call each for R2/R3,
  using preserved classified source and normal template/examples; no retry or
  selection of passing repetitions. Unknown/participant boundaries need separate
  evaluation. A combined two-voice audio file in one Zoom guest is one actual
  participant source, not evidence of a two-participant meeting.

## Failure behavior

Malformed/oversized fields or source refs fail before note persistence. A value-free
`BotNoteContractInvalid` is excluded through Celery 5.5.1's `dont_autoretry_for` on
this summarize task only; the task fails rather than reporting successful empty
work. Existing ordinary/transport retries and SDK policy remain unchanged. Tests
exercise the installed task wrapper, actual PostgreSQL persistence and replay.
The current UI has no dedicated bot-contract error. For first generation the note
stays absent/not-started and detail says “No note for this visit yet.” On later
failure an earlier saved note remains untouched.
Any upload-only local processing indicator expires after 15 minutes. This is an
operational limitation, not a successful or automatically recovering draft.

## Release and recovery

GitHub Actions remains disabled. No staging apply is authorized by this document.
After final review and an explicit selection, build immutable merged current trunk,
reconcile actual worker/web invocation and deployed source/config/schema, preserve
Session Prep and unrelated drift, then review a narrow rollout packet. No production.
Recovery is a reviewed current-trunk fix that removes the pilot hint/branch while
preserving all later source/config/schema changes. Do not roll back to an old image
or rewrite existing notes/settings. Preserve every failed output and review receipt.
