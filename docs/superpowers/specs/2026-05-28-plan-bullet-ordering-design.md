# Design — Plan bullet ordering: patient todos before doctor todos

**Issue:** #33 (item 3 of 6)
**Date:** 2026-05-28
**Author:** Oscar (with Claude)

## Background

Third item in issue #33, requested by both Pearson (4/4/25) and Jensen (5/5/25): "plans should be sorted by patient and doctor todos". Currently the Plan section of a generated clinical note groups bullets by condition (instruction #3 in `build_structured_prompt` at `app/jobs/summarize.py:235-240`), but within a single condition's bullets the ordering of patient-actionable vs doctor-actionable items is arbitrary.

The boss already confirmed priority order on items 1+2 → 3 → 4-6, with one PR per item. Items 1+2 shipped in PR #60 (commit `cbf0c50` on master). This is the next item.

## Constraints

- **Single condition vs multiple conditions.** The existing instruction #3 groups Plan bullets by condition first (diabetes block, hypertension block). The boss confirmed in brainstorming that the new ordering applies **within each condition's block**, not across conditions. So a multi-condition Plan still reads: condition A's patient todos, condition A's doctor todos, condition B's patient todos, condition B's doctor todos, etc.
- **No new data model.** Patient-actionable vs doctor-actionable is already a distinction the model handles in `build_action_item_prompt` (`app/jobs/summarize.py:252-286`) which extracts action items separately. We're just instructing the structured-prompt template to use that distinction for ordering.
- **Live-LLM eval gating.** Same as previous PRs: any eval test we add is gated by the existing module-level `pytestmark = pytest.mark.skipif(not (_RUN_LIVE_LLM_TESTS and _HAS_AZURE_OPENAI_CREDS))` in `tests/job/summarize_test.py`. CI default is skip.
- **Judge-model deployment 404.** The judge model `gpt-4.1-mini-2025-04-14` (`tests/eval.py:8`) is not deployed on staging Azure OpenAI. This affects local verification of the eval pass/fail but not the merit of the test. Verification falls back to a manual `/tmp/manual_verify_plan_order.py` script the same way PRs #59 and #60 handled it. Out of scope for this PR; tracked separately.

## Approach

Single-PR prompt-text change: add a new sub-bullet to instruction #3 in `build_structured_prompt`. No renumbering required (sub-bullet sits inside existing #3, which already has 5 sub-bullets). Add one new live-LLM eval test in `tests/job/summarize_test.py` following the same pattern as the items 1+2 tests.

### 1. Prompt change

In `app/jobs/summarize.py`, instruction #3 in the structured prompt currently has 5 sub-bullets covering grouping by condition, condition ordering, condition consistency across sections, and a "flat list" prescription. Add a 5th sub-bullet (right before the existing "Do not create subsections" line) targeting the Plan section specifically:

```
- In the Plan section specifically, within each condition's bullets, list patient-actionable items (e.g. medications to take, lifestyle changes, symptoms to monitor, follow-ups the patient must initiate) before doctor-actionable items (e.g. labs to order, referrals to send, follow-up calls the doctor must place). This makes it easier for the patient to scan their own to-dos.
```

The sub-bullet is intentionally scoped to "the Plan section specifically" because the other sections (Subjective, Objective, Assessment) don't have actionable bullets in the same sense.

### 2. Eval test

Add `test_plan_bullets_sort_patient_before_doctor` to `tests/job/summarize_test.py`. The test uses an in-memory transcript (no new seeds) crafted to elicit exactly one condition (diabetes management) with a clear mix of patient and doctor todos. Criteria for `eval_n_of_m` validates that in the Plan section, the patient-actionable bullets appear ahead of the doctor-actionable bullets.

Test transcript outline:
- `Patient:` reports rising blood sugar
- `Doctor:` reviews values, prescribes a new medication
- `Doctor:` tells the patient: take the new medication daily, test blood sugar twice a day, avoid late-evening meals
- `Doctor:` will order an HbA1c lab and send a referral to a nutrition counselor

Expected post-fix Plan ordering (within the diabetes condition block):
1. Patient: take new medication daily
2. Patient: test blood sugar twice a day
3. Patient: avoid late-evening meals
4. Doctor: order HbA1c lab
5. Doctor: send referral to nutrition

Criteria text for `eval_n_of_m`:
> "In the Plan section of this clinical note, all patient-actionable bullets — items the patient must do themselves, such as taking medication, testing blood sugar, or changing diet — appear BEFORE the doctor-actionable bullets — items the doctor will do, such as ordering labs, sending referrals, or scheduling follow-ups. The patient bullets and the doctor bullets do not need to be labeled as such; the test is about reading order."

Gated by the existing `pytestmark`. Reuses the seeded `Ocie Dosie` patient + Test SOAP Note schema + example note pattern from previous tests, so no new seeds.

### 3. Manual verification fallback

`/tmp/manual_verify_plan_order.py`: small one-off Python script that constructs the same in-memory transcript, calls `generate_clinical_note`, and prints the Plan section so we can eyeball the order. Used to establish baseline (before the prompt change) and to confirm the fix took effect. Not committed.

### 4. Out of scope

- Items 4–6 of issue #33: separate PRs, one each.
- Re-evaluating instruction #3's condition-grouping rules. The new sub-bullet only **adds** an ordering rule scoped to Plan; existing condition-grouping rules apply unchanged.
- Fixing the judge-model 404. Tracked separately.
- Adding the manual-verify scripts to `scripts/`. Same call as in PR #60 — defer to a cleanup if more accumulate.

## Risks and rollback

- **Risk: model misclassifies a bullet as patient-actionable when it's actually doctor-actionable.** Categories are inherent in the model's understanding. If a bullet is ambiguous ("monitor for signs of infection" could be either), the model picks one. Mitigation: the existing `build_action_item_prompt` separates patient vs doctor explicitly with examples; the new instruction borrows the same categorization, so consistency is likely.
- **Risk: noise in Plan section ordering across runs.** The eval `n=1, m=2` budget (matching items 1+2) tolerates one bad run out of two. If actual flakiness exceeds that, the test will surface it.
- **Rollback:** revert one commit. No DB migration, no FE change, no schema change.

## Affected files

- `app/jobs/summarize.py` — instruction #3 in `build_structured_prompt` only.
- `tests/job/summarize_test.py` — one new test function appended.
- No `tests/seeds.py` change.
