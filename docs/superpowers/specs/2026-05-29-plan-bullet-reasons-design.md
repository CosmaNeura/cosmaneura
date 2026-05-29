# Design — Plan bullets must include the reason for each action

**Issue:** #33 (item 5 of 6)
**Date:** 2026-05-29
**Author:** Oscar (with Claude)

## Background

Fifth item in issue #33, requested by Jensen (5/5/25): "plan bullet points should include the reason for the action, e.g. 'do X for Y', or 'do X to Z', so that if another doctor read the note they would know why things were being done." A clinician picking up an unfamiliar chart needs to understand *why* each step is there — pure imperatives ("Order MRI", "Take ibuprofen 600mg TID") don't carry the reasoning forward.

Today the model includes reasons inconsistently. From the post-item-4 baseline of the diabetes Plan section:

```
- Start empagliflozin 10mg once daily in the morning, beginning tomorrow.
- Check blood sugar twice daily ... keep a log for review at next visit.
- Avoid late-evening meals; finish dinner by seven to help reduce postprandial hyperglycemia.
- Order HbA1c lab to assess current glycemic control.
- Refer to nutrition counseling for meal planning and dietary support.
```

3 out of 5 bullets include a "for X" / "to X" clause; the first two don't. The variance is exactly what Jensen wants to eliminate.

Items 1+2 shipped in PR #60 (merged). Items 3 (PR #61) and 4 (PR #62) are in flight. This work stacks on PR #62.

## Constraints

- **Strict interpretation, per user decision.** Every Plan bullet must include a stated reason — even ones where the rationale feels obvious in context ("Continue metformin" needs "for ongoing diabetes management"). This is the literal reading of Jensen's complaint.
- **No new schema field.** The Plan section's content rules live in the prompt; we're tightening guidance.
- **Plan section only.** Other sections (Subjective, Assessment, Objective, Medications, etc.) don't have the same "list of actions" structure where a reason is expected. The instruction is scoped.
- **Live-LLM eval gating** + **judge-model 404** — same workaround as previous PRs.

## Approach

Single-PR prompt-text change adding one new numbered instruction (item 11) between current items 10 (Medications) and 11 (action items). Renumbers existing items 11-14 to 12-15. One new live-LLM eval test using an in-memory lumbar-strain transcript with several distinct actions, each of which should pick up an explicit reason.

### 1. Prompt change

Insert as new item 11:

```
11. Plan section bullets MUST include the reason for each action -- the "why" -- using "for X" or "to X" phrasing. For example: "Order MRI to rule out disc pathology" rather than "Order MRI"; "Continue metformin for ongoing diabetes management" rather than just "Continue metformin"; "Avoid heavy lifting to prevent re-injury" rather than just "Avoid heavy lifting". This applies to EVERY Plan bullet, even when the rationale seems obvious -- a doctor reading the note for the first time will not have the same context. When the transcript does not explicitly state the reason, infer it from the clinical context (the patient's complaint, the assessment, the medication's indication). Do not invent reasons unrelated to the visit.
```

Renumber items 11-14 → 12-15 (action items, Imitate, JSON, deep breath).

### 2. Eval test

Add `test_plan_bullets_include_reason` to `tests/job/summarize_test.py`. Transcript: patient reports lower back pain; doctor diagnoses lumbar strain, prescribes ibuprofen, recommends heat application, restricts heavy lifting, orders MRI.

Expected post-fix Plan bullets:
1. Take ibuprofen 600mg TID **for pain control / to reduce inflammation**
2. Apply heat 20 minutes a few times a day **to reduce muscle tension / for muscle relaxation**
3. Avoid heavy lifting **to prevent re-injury**
4. Order MRI **to rule out disc pathology** (or similar)

Criteria for `eval_n_of_m`:

> "Every bullet in the 'Plan' section of this clinical note must include an explicit reason for the action -- a 'for X' or 'to X' clause explaining the purpose. Acceptable formats: 'Order MRI to rule out disc pathology', 'Take ibuprofen 600mg three times a day for pain control', 'Continue ibuprofen for inflammation'. UNACCEPTABLE: bullets that state only the action with no reason, e.g. 'Order MRI' or 'Take ibuprofen 600mg three times a day'. If ANY Plan bullet lacks a stated reason, the test fails. Reasons are clinically grounded (related to the patient's complaint or the standard indication for the action), not invented."

Gated by the existing `pytestmark`.

### 3. Manual verification fallback

`/tmp/manual_verify_plan_reasons.py`: same pattern as previous items. Constructs the in-memory transcript, calls `generate_clinical_note`, prints the Plan section, and prints a per-bullet "has reason?" check (heuristic: looks for "for" / "to " / "to:" / "for:" within each bullet).

### 4. Out of scope

- Item 6 (ROS vs Subjective) of #33 — separate PR.
- Tightening Subjective, Assessment, or other sections to include reasons. Plan is the only actionable section where reasons matter clinically.

## Risks and rollback

- **Risk: model over-extends and writes verbose reasons that bloat the Plan section.** Mitigation: the instruction is specific ("for X" / "to X" — short phrases). If verbosity drifts, follow-up tightening can constrain it.
- **Risk: model invents reasons when the transcript doesn't state them.** The instruction includes "Do not invent reasons unrelated to the visit." But the model may still confabulate. Mitigation: spot-check field feedback after merge.
- **Risk: "Continue X" bullets get padded with rote reasons.** "Continue X for ongoing X management" is borderline tautological. Acceptable trade-off — Jensen's point is that the next reader doesn't have context.
- **Rollback:** revert one commit. No DB migration, no FE change.

## Affected files

- `app/jobs/summarize.py` — insert new item 11 in `build_structured_prompt`, renumber 11-14 → 12-15.
- `tests/job/summarize_test.py` — append one new test function.
- No `tests/seeds.py` change.
