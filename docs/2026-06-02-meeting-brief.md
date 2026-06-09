# Engagement Engine — full context brief for the 2pm

**Date:** 2026-06-02 · **For:** Oscar's meeting with the boss
**Purpose:** the whole arc in order — what the boss originally wanted, what we did to test it,
the experiments, the v2 redesign, and what we want to build.

---

## 0. The 30-second version

The boss handed us a prototype that guesses a patient's **personality** from their visit text
and turns it into a "how to talk to this patient" hint. We evaluated it: technically clean, but
on our real transcripts it was **useless** — it said "Low confidence" for *everyone* and gave
almost everyone the same two labels. The boss's read was "our patients self-select into a type."
We tested that with data: he's **partly right** (our patients really do cluster), but the
"Low confidence / same labels" problem turned out to be **the tool, not our patients**. Then a
**v2 redesign** arrived that stops guessing personality and instead reads **observable
communication behaviour** (does the patient want to lead the decision? do they ask for details?)
and maps it to a **validated 3-role clinical taxonomy**. We tested v2 on the same transcripts:
it **works** — confidence jumps from 0% usable to ~74% usable. We want to build v2, with one
hard gate: validate it against real ground truth before any clinician sees it.

---

## 1. What the boss originally wanted

He passed us a prototype ("Cosmas Engagement Engine") — a small Python/FastAPI service that:
- reads a patient's de-identified intake/visit text,
- estimates the **Big Five (OCEAN)** personality traits,
- maps them to one of **5 "engagement styles"** + a confidence level,
- framed explicitly as a **communication aid** (how to talk to the patient), **not** a diagnosis.

**The ask:** evaluate it and decide **GO / NO-GO on integrating it** into cosmaneura — is it
worth wiring in, and how? The real question underneath was a product/ethics one: *do we want to
infer and show patient personality at all?*

---

## 2. What we did to validate it (v1 evaluation — 2026-06-01)

Documented in `docs/2026-06-01-engagement-engine-decision.md`.

- **Code review:** clean, typed, tested, honest about its limits. No blocking bugs.
- **Model swap:** the prototype shipped on Anthropic Claude; we re-ran it on **our** Azure
  OpenAI (gpt-4.1) — the model we already pay for. Worked; no need to add Anthropic.
- **Benchmark (public Pennebaker & King essays, n=80):** Big Five accuracy **~0.56 macro** —
  in the normal literature range (0.55–0.65). BUT Neuroticism landed at chance (0.475), and the
  headline **confidence came out "Low" for almost everyone** by construction of its formula.
- **Real-data validation (read-only over `ai_visit_transcript_non_phi`, de-identified):**
  - **100% Low confidence** on real visits — *not* because text was short (evidence was "High").
  - Predictions **skewed heavily to 2 of 5 styles** (the high-Agreeableness ones:
    Caretakers / Guided Trusters).
  - The cheap fix we hypothesized — feed only the patient's turns, drop the doctor's — **made
    it slightly worse**. Ruled out by an A/B test.

**Verdict v1:** integration is easy (~1.5–2.5 wks), but the *value* depends on recalibration
that's hard and uncertain. Don't integrate yet. We wrote this up as the findings PDF the boss saw.

---

## 3. The boss's reaction → the self-selection hypothesis (2026-06-02)

He loved the analysis and said: *"I think our patient population self-selects into certain
patient types — so the skew isn't entirely unexpected. Let's brainstorm an experiment."*

That's a real, testable hypothesis: maybe the engine is fine and our patients genuinely *are*
all one type. So before the brainstorm, we ran experiments to tell that apart from "the tool
just can't separate people."

---

## 4. The experiments (2026-06-02) — `docs/2026-06-02-engagement-experiments.md`

All read-only / no PHI copied; reused the same engine on our Azure OpenAI.

**A. Discrimination control — can the engine separate at all?**
- Fed it 6 *extreme* hand-written personalities + 40 diverse labeled essays.
- The engine **has range** (it scored Agreeableness from 10 to 95 when the text was clear) and
  hit 4–5 different styles — so it *can* discriminate on strong signal.
- BUT confidence stayed **100% Low even for cartoonish extremes**, and a *diverse* essay corpus
  **also collapsed to 2 styles** (just different ones).

**B. Population variance — are our patients actually a narrow type?**
- Our patients genuinely cluster **high on Agreeableness (64) and Conscientiousness (66)** —
  almost nobody scores Low. Essays sit lower and more centered. So **the boss is right that our
  patients read as a distinct type.**
- But the spread is narrow *everywhere* (the model compresses everyone), so a narrow spread
  alone isn't proof of self-selection.

**C. Fresh patient run (n=37):** reproduced it — Caretakers 68% / Guided Trusters 27%, 100% Low.

**A2. The untested lever — intake free-text:** ruled out by data. Prod has only **9 answered
intakes averaging 53 words** — far too little; confidence collapsed to 0.056 and the scores
went to the default. "Freer register = more signal" didn't materialize.

**Recalibration spike (offline, no new calls):**
- Lowering the model's `tau` to force higher confidence is **cosmetic** — it makes *everything*
  confident, not just the separable cases. Don't do it alone.
- **Re-centering the prototypes onto our patient cluster DOES fix the style skew** cheaply (all
  5 styles appear) — the skew was an artifact of prototypes built for a different population.
- But **nothing honestly fixes the Low confidence**: if patients truly cluster into one narrow
  type, "Low confidence" is the *correct* answer.

**What the experiments settled:**
1. **"Low confidence for everyone" is structural — the tool, not our patients.** (Proven: even
   extremes and a diverse control get 100% Low.)
2. **"Same 2 labels" is mostly the tool too** (a diverse corpus collapses as well), and the
   *skew direction* is cheaply fixable by re-centering.
3. **The boss is right that our patients are a distinct type** (high A/C) — but whether that's
   *self-selection* or just *the polite clinical setting* is **confounded and unprovable from
   transcripts** — it needs ground truth (a self-report from patients).

---

## 5. The v2 redesign (arrived 2026-06-02) — `docs/2026-06-02-engagement-v2-review.md`

A full v2 (spec + code + 28 tests) was handed over — already decided, to build against. It
responds directly to our findings:

- **Stops estimating personality.** The diagnosis: v1's 5 styles separated mainly on the
  *least observable* traits (A/C/N), which a transcript can't carry (SOKA model). Correct and sharp.
- **New output: 3 communication roles** from the **Control Preferences Scale** (a validated,
  widely-used clinical measure with published norms ~26% active / 49% collaborative / 25% passive):
  **Self-Directed / Collaborative / Directed.** Scored on **observable behaviour** — how much
  decision control the patient seeks, how much information they engage with — plus a worry flag.
- **Per-practice recalibration (R3):** standardize the read against the local patient population
  to counter the range-restriction we found.
- **One ICE intake question** ("what's bringing you in, what worries you, what are you hoping
  for") — does double duty: sets the agenda *and* gives the "preferred role" signal.
- **Two channels + the preference-enactment gap:** intake = *preferred* role; visit = *enacted*
  role; the contrast (e.g., wants involvement but was directed → "suppressed") is the signal.

**We tested v2 on our own real transcripts (n=38, read-only):**

| | v1 (personality) | v2 neutral (day 1) | v2 recalibrated |
|---|---|---|---|
| mean confidence | 0.19 | **0.471** | 0.456 |
| reach usable (Moderate+) | **0%** | **74%** | 76% |
| roles | collapsed to 2 | Collaborative 74% / Self-Directed 26% | Collaborative 39% / Directed 39% / Self-Directed 21% |

**v2's central promise holds** — usable confidence where v1 gave 100% Low, and it's *not*
cosmetic (the win is the reframe, not tau-fiddling).

**Three honest caveats:**
1. **Confidence ≠ validity.** There's no ground-truth corpus for CPS roles, so we've shown v2 is
   *confident and plausible*, not *correct*. Showing clinicians a confident-but-unvalidated label
   is a *bigger* risk than v1's honest "Low." → validation pilot is now **more** important.
2. **Recalibration makes the role relative-to-practice** ("wants less control than your typical
   patient"), not absolute. Under a neutral baseline, *nobody* reads as Directed; the 3-way
   spread depends entirely on recalibration. Must be understood and disclosed.
3. **The gap feature can't be tested yet** — needs the ICE intake question shipped + data
   (today: 9 intakes, 53 words).

---

## 6. What we want to code (the build plan)

Vendorize v2 into `app/` (not a separate microservice), reusing what we already validated:

1. **Vendor + Azure adapter** — move the package into `app/engagement/`, implement the
   `LLMClient` against our Azure OpenAI (already proven to work). *(S/M)*
2. **Persistence** — new `engagement_role` table (the role, confidence, the indicators, the
   per-practice baseline used, and versions for audit) + Alembic migration. *(M)*
3. **Celery job** — score the visit transcript after it's ready (the *enacted* read), same
   pattern as `summarize.py`. Maintain the **rolling per-practice baseline** (R3). *(M)*
4. **Intake change** — add the **single ICE question** to the intake flow → unlocks the
   *preferred* read and the gap (this is a product change, needs the boss's OK). *(S, product)*
5. **API** — expose the role in `/internal/v1/notes/{id}`; regen the OpenAPI client. *(S)*
6. **Frontend panel** — role + confidence band + the two indicator bars + worry flag + gap,
   with the disclaimer and the relative-to-practice note. *(M)*
7. **THE GATE — validation pilot** — administer the real CPS (1–2 items) to a consented internal
   cohort, compare to the engine's read, track preference-enactment concordance vs the ~61%
   literature benchmark, and a one-tap clinician "did this fit?" feedback loop. **Before** any
   clinician-facing rollout.

Estimate: ~1.5–2.5 weeks of engineering for an internal pilot, plus the validation pilot running
in parallel (its critical path is consent/compliance + accruing patients, not code).

---

## 7. Decisions to get from the boss today

1. **GO to build v2** as an internal, opt-in pilot (never patient-facing, never driving care)?
2. **Approve the CPS validation pilot as a hard gate** before clinicians see labels — and who
   owns consent/compliance for it?
3. **Ship the single ICE intake question?** (product change; also the only way the gap feature
   becomes real.)
4. **PHI handling:** score the PHI transcript inside the job (same exposure as `summarize.py`,
   simplest) — needs a quick compliance sign-off.
5. **Per-provider vs global:** keep the role taxonomy global for v1 (revisit per-provider later)?
6. **Disclosure:** agree the UI states the role is *relative to this practice* and is a
   communication aid, not an assessment.

---

## 8. Numbers cheat-sheet (if asked)

- v1 essays benchmark: 0.56 macro accuracy; Neuroticism 0.475 (chance).
- v1 on real transcripts: 100% Low confidence; skew to Caretakers/Guided Trusters.
- Our patients: Agreeableness 64, Conscientiousness 66 (a real, narrow cluster).
- Intake data today: 9 answered intakes, ~53 words avg (too sparse).
- v2 on real transcripts: 74% reach usable confidence (vs 0% v1), mean 0.47.
- CPS published norms: ~26% active / 49% collaborative / 25% passive; ~61% preference-enactment
  concordance.

---

## 9. The one-liner to open with

*"You were right that our patients read as a distinct type. I tested it: but the things that made
v1 look broken — always 'Low confidence', always the same labels — turned out to be the tool, not
our patients. The v2 redesign that came in fixes that at the design level: it reads how patients
communicate instead of guessing personality, and on our own transcripts it goes from 0% to ~74%
usable confidence. I want to build it — with one hard rule: we validate it against real patient
responses before any clinician sees a label, precisely because now it looks confident."*
