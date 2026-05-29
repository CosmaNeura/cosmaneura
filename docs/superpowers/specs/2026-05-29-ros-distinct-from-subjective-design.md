# Design — Review of Systems must add doctor insight, not repeat Subjective

**Issue:** #33 (item 6 of 6 — last item)
**Date:** 2026-05-29
**Author:** Oscar (with Claude)

## Background

Final item in issue #33, requested by Jensen (5/5/25): "review of systems should not just repeat the subjective section, it should include the additional insights that are gleaned by the doctor after the patient's initial description of the problem." Today the model — when the schema has an ROS field — tends to paraphrase the Subjective section into ROS, so the same information appears twice and the ROS section adds zero clinical value.

ROS is clinically a *structured* survey: the doctor asks system-by-system targeted questions ("any chest pain? any shortness of breath? any nausea/vomiting/diarrhea?") and records both positives and pertinent negatives. Subjective is the patient's narrative — what brought them in, in their own words. The two should complement, not duplicate.

Items 1+2+3+4+5 are now merged into master via PRs #60, #61, #62, and (about to be) #63. This is the last of issue #33.

## Constraints

- **The seeded "Test SOAP Note" schema has no ROS field.** All existing eval tests use that schema. We can't add ROS to it without disturbing the existing tests. Per the user's brainstorming decision, the new eval test constructs its **own DoctorNoteSchema in memory** with a custom `fields` dict that includes a "Review of Systems" entry. No `tests/seeds.py` change.
- **The prompt instruction must be conditional.** Most real schemas may or may not have an ROS field. The instruction reads "WHEN the schema includes a Review of Systems section..." — a no-op for schemas without ROS, an active rule for schemas with ROS.
- **Subjective constraint from item 1 still applies.** Subjective sources only from `Patient:` lines (PR #60). The new ROS rule adds that ROS sources from `Doctor:` questions and the patient's responses to those targeted questions — specifically the system-by-system survey.
- **Live-LLM eval gating** + **judge-model 404 on staging** — same workaround as previous PRs.

## Approach

Single-PR prompt-text change inserting a new numbered instruction (item 12) between current items 11 (Plan reasons) and 12 (action items). Renumber existing items 12-15 to 13-16. One new live-LLM eval test in `tests/job/summarize_test.py` using an in-memory schema with a ROS field and an in-memory transcript with a clear initial complaint plus a multi-system ROS survey.

### 1. Prompt change

Insert as new item 12:

```
12. Review of Systems (ROS) section: when the schema includes a "Review of Systems" field (or similarly-named system-by-system survey field like "ROS"), populate it with the doctor's system-by-system findings from targeted questions asked during the visit -- both positives and pertinent negatives. The ROS section MUST NOT be a paraphrase or summary of the Subjective section. The patient's initial chief complaint and narrative go in Subjective; the doctor's follow-up "any chest pain? any nausea? any headaches?" survey responses go in ROS, organised by system (HEENT, Respiratory, Cardiovascular, GI, GU, Musculoskeletal, Neuro, Skin, Psych, etc.). If the doctor did not ask explicit ROS questions, write "Not performed" or "Deferred"; do not pad ROS with restated Subjective content.
```

Renumber items 12-15 → 13-16 (action items, Imitate, JSON, deep breath).

### 2. Eval test

Add `test_review_of_systems_distinct_from_subjective` to `tests/job/summarize_test.py`. The test constructs:

- An **in-memory `DoctorNoteSchema`** with fields `CC`, `Subjective`, `Review of Systems`, `Assessment`, `Plan`. The schema is NOT saved to the DB — `generate_clinical_note` only reads `.fields` and `.doctor_id`.
- An **in-memory transcript** where:
  - Patient initially describes a 3-day dry cough with low-grade fever
  - Doctor performs a targeted ROS: asks about chest pain, dyspnea, GI symptoms, sore throat, headache
  - Patient gives system-by-system answers (mild dyspnea on exertion, sore throat yes, no GI symptoms, no headache)

Expected post-fix:
- Subjective: 3-day history of dry cough, low-grade fever (the patient's initial narrative)
- ROS:
  - HEENT: mild sore throat; no headache
  - Respiratory: mild dyspnea with stair climbing; cough as in HPI
  - Cardiovascular: no chest pain
  - GI: no nausea, vomiting, or diarrhea

Criteria for `eval_n_of_m`:

> "This clinical note's 'Review of Systems' section must contain the doctor's system-by-system findings from targeted questions — pertinent positives and negatives organised by system (e.g. 'HEENT: mild sore throat', 'Respiratory: mild dyspnea on exertion', 'GI: denies nausea/vomiting/diarrhea', 'Cardiovascular: no chest pain'). It MUST NOT be a paraphrase or restatement of the Subjective section. If the Review of Systems section reads as a narrative summary of what the patient initially said (3-day dry cough, fever) rather than organised findings from the doctor's targeted survey, the test fails."

Pass `example_note=None` to `generate_clinical_note` since the inline schema has no associated example.

### 3. Manual verification fallback

`/tmp/manual_verify_ros.py`: small script that constructs the same in-memory schema + transcript, calls `generate_clinical_note`, prints Subjective and Review of Systems side by side so we can eyeball them. Used to baseline and confirm. Not committed.

### 4. Out of scope

- This is the last item of #33. After merge the whole issue can close.
- Migrating any real DoctorNoteSchema to include ROS. Per-doctor schema choice.
- Tightening Subjective beyond item 1's sourcing rule.

## Risks and rollback

- **Risk: ROS still leans on Subjective when the doctor's questions are sparse.** If the transcript has no targeted ROS questions, the model may invent a "ROS" rather than write "Not performed". Mitigation: the instruction explicitly authorises "Not performed" / "Deferred" as the correct output in that case.
- **Risk: ROS becomes a wall of "denies X, denies Y" beyond what was actually asked.** Pertinent negatives must come from actual questions, not hallucination. The Subjective-from-Patient-lines rule (item 1) limits sourcing for Subjective; the new ROS rule applies similar sourcing for ROS (from `Doctor:` questions + the patient's responses). The wording emphasises "from targeted questions asked during the visit".
- **Risk: Schema field naming variation.** Real doctors might call it "ROS", "Review of Systems", "Systems Review", etc. The instruction lists "ROS" as an alias. Other names rely on the model's reading. Field-feedback signal post-merge.
- **Rollback:** revert one commit.

## Affected files

- `app/jobs/summarize.py` — insert new item 12 in `build_structured_prompt`, renumber 12-15 → 13-16.
- `tests/job/summarize_test.py` — append one new test function constructing an in-memory schema.
- No `tests/seeds.py` change.
