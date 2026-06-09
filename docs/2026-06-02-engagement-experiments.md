# Engagement Engine — Experiments for the self-selection question

**Date:** 2026-06-02 · **Author:** Oscar · **For:** afternoon brainstorm
**Context:** Boss's reaction to the 06-01 brief: *"I think our patient population
self-selects into certain patient types."* We ran three experiments to test that
hypothesis with data before deciding what experiment to run together.

All runs use our Azure OpenAI gpt-4.1, the same engine as the 06-01 brief.
Patient data: **read-only** over `ai_visit_transcript_non_phi` (already de-identified;
nothing copied locally). No PHI left the existing perimeter.

---

## TL;DR for the meeting

The boss is **partly right, and the data sharpens where.** Two symptoms from the
06-01 brief had been lumped together; the experiments split them apart:

1. **"Low confidence for ~100% of patients" is STRUCTURAL, not our population.**
   We proved it: even cartoonishly extreme personalities and a maximally diverse
   essay corpus *also* get 100% Low confidence. Self-selection cannot explain a gauge
   that reads "Low" for literally everyone. The confidence formula is the problem.
2. **"Collapses to ~2 styles" is also mostly the engine, not our patients.**
   A diverse labeled essay corpus collapses to 2 styles too — just *different* ones.
   So "collapse" is a property of the 5-prototype geometry, not unique to us.
3. **WHERE the boss is right:** our patients genuinely cluster **high on
   Agreeableness + Conscientiousness** (means 64 / 66; almost nobody scores Low),
   while essays sit lower and centered. That real population signal exists — the
   engine just can't express it as confidence/separation. *But* whether that cluster
   is **self-selection** or just the **polite clinical register** is **confounded and
   unfalsifiable from transcripts alone** — it needs ground truth.

**Punchline to open with:** *"I tested your hunch. You're right that our patients read
as a distinct type — high agreeableness and conscientiousness. But two of the things
that made the demo look broken (always 'Low confidence', always the same couple of
labels) turn out to be the engine, not our patients — it does that to everyone,
including a diverse control group. So the experiment worth running isn't 'are our
patients similar' — we can see that they read that way — it's 'can we recalibrate the
engine to be useful, and can we validate it against real ground truth.'"*

---

## Experiment A — Discrimination control (does the engine separate at all?)

Fed the engine inputs we KNOW span the personality space.

**6 extreme hand-written personalities:**

| Probe | Segment | Confidence | OCEAN (O/C/E/A/N) |
|---|---|---|---|
| hostile, low-A, low-C | Free Spirits | **Low** 0.250 | 55 / 20 / 45 / **10** / 60 |
| organized striver | Strivers | **Low** 0.222 | 55 / **95** / 55 / 50 / 20 |
| skeptical weigher | Weighers | **Low** 0.233 | 80 / 65 / 45 / 35 / 50 |
| anxious introvert | Caretakers | **Low** 0.132 | 50 / 40 / 25 / 50 / **90** |
| impulsive free spirit | Free Spirits | **Low** 0.268 | 85 / **20** / 55 / 50 / 50 |
| warm caretaker | Caretakers | **Low** 0.175 | 45 / 55 / 50 / **75** / 70 |

- **The engine HAS dynamic range:** Agreeableness 10→75, Conscientiousness 20→95,
  Neuroticism 20→90. It clearly reads strong signal. It hit 4 distinct segments.
- **But confidence stayed Low for all 6** — even the best-separated probe (top softmax
  0.665) yields a Low headline. The ceiling is mathematical: `headline = separation ×
  trait_conf × evidence_factor`, and with 5 prototypes + `_SOFTMAX_TAU=22` the top
  probability rarely clears the ~0.62 you'd need for "Moderate." → **structural floor.**

**40 essays (diverse, human-labeled corpus):**
- Segments collapsed to **Free Spirits 55% + Caretakers 42%** (Strivers 2%) — 97% in
  two styles, **100% Low confidence**. Same failure shape as transcripts, *different*
  two styles. → collapse + Low-confidence are NOT about our patients.
- Engine only weakly tracks the ground-truth labels (label-high vs label-low mean-score
  gaps): E +7.0, O +4.2, C +3.6, A +3.3, **N +0.4 (≈chance)** — consistent with the
  0.475 Neuroticism accuracy in the 06-01 benchmark. Coarse instrument on natural text.

## Experiment B — Population variance (are our patients narrower?)

OCEAN spread (std) from the same engine — patients (fresh n=37) vs essays (n=40):

| Trait | Patients mean ± std | Essays mean ± std |
|---|---|---|
| Openness | 61.9 ± 7.8 | 60.6 ± 8.7 |
| Conscientiousness | **65.6** ± 7.6 | 47.4 ± 10.5 |
| Extraversion | 50.0 ± 6.5 | 54.6 ± 9.0 |
| Agreeableness | **64.2** ± 7.0 | 52.0 ± 9.2 |
| Neuroticism | 60.4 ± 11.8 | 67.9 ± 9.6 |

- Spread is **narrow everywhere** (~±7–11), including on the diverse essays → gpt-4.1
  compresses OCEAN toward the middle on *any* real text. A narrow patient spread is
  therefore **weak** evidence for self-selection on its own.
- The real patient signature is **location, not spread**: patients shifted UP on
  Conscientiousness (+18) and Agreeableness (+12) vs essays. **Nobody scored Low on
  O/C/A**; 33/37 High on C, 32/37 High on A. *This* is the boss's "patient type" — real,
  but confounded with the cooperative medical register (the probes show the engine can
  read low-A when text is hostile, so high-A here is register-or-population, not floor).

## Experiment A2 — intake-text lever (de-identified on the fly) — RULED OUT

The 06-01 brief flagged scoring the intake-chatbot text (patient's own words, freer
register, no doctor turns) as the ONE input lever never tried. Tested it: read-only pull
of `intake` rows from prod, patient ANSWERS only, de-identified via Azure DeID (prod
creds), scored via `intake_text`.

**Blocked by data sparsity, not by the engine:**
- Prod has **34 intakes, only 9 with any answer**; answered text averages **53 words**
  (min 22, max 108) — below the ~75 the engine needs for solid evidence.
- Result (n=9): segments Guided Trusters 56% / Caretakers 22% / Free Spirits 22%;
  **100% Low confidence, mean 0.056** (lower than visits' 0.199); evidence band 7 Low /
  2 Moderate.
- OCEAN **collapses to the default prior** — Openness 45.3 **± 0.9** (near-constant),
  everyone ~Average, almost no Low/High. With this little text the engine isn't reading
  personality, it's returning the neutral fallback.

→ The "freer register = more signal" hope does not materialize: too little text = no
signal, and confidence is *worse* than on visits. **Intake-text is ruled out by evidence**,
like patient-only filtering was on 06-01. All three cheap levers are now exhausted; only
recalibration + ground-truth validation remain.

## Experiment C — fresh patient run (n=37, patient-only, read-only prod)

- Segments: **Caretakers 68%, Guided Trusters 27%, Strivers 5%** — reproduces the
  high-Agreeableness skew from 06-01.
- Confidence: **100% Low** (0.105–0.299, mean 0.199).
- Confirms the 06-01 finding on a fresh seed/sample.

---

## What this means for the experiment we design together

The transcripts **cannot** settle self-selection (it's confounded with register and
there's no ground truth). The experiments that actually move the decision:

1. **Ground-truth pilot (the real test of the boss's hypothesis).** Have a small
   consented cohort self-report a short Big Five (e.g. TIPI-10) at intake. Then:
   (a) is the *population* genuinely high-A/high-C? → tests self-selection directly;
   (b) does the model agree with self-report? → tests validity. Only this separates
   "our patients are a type" from "the register makes everyone look like a type."
2. **Recalibration spike (cheap, no patients needed).** Retune the confidence formula
   + `_SOFTMAX_TAU` so the gauge isn't structurally always Low, and recenter the
   segment prototypes for the clinical high-A/high-C region instead of essay space.
   Re-validate. Addresses findings #1 and #2 above.
3. **Intake-text path (still untested).** Score the intake chatbot free-text
   (de-identified on the fly) — patient's own words, freer register, no doctor turns.
   The one input lever we haven't tried.

**Caveats (say them out loud):** n=37, single seed, no ground truth; gpt-4.1 compresses
OCEAN generally; segment labels are an unvalidated heuristic mapping. These are
directional findings, not a validation.

---

## Recalibration spike — "recalibrate what?", answered offline (n=42, no new Azure calls)

Re-mapped the patient OCEAN vectors under varied `_SOFTMAX_TAU` and prototype placements
(`phase0/recalibrate_sweep.py`). Patient centroid: O61 C65 E50 A64 N59. Baseline as
shipped: **42/42 Low**, mean headline 0.193, styles skewed (Caretakers 25, Guided
Trusters 12, Strivers 3, Weighers 2; no Free Spirits).

**Knob #1 — lower `_SOFTMAX_TAU` (confidence formula): COSMETIC.**

| tau | patients out of "Low" | patient sep | probe sep (control) |
|---|---|---|---|
| 22 (current) | 0/42 | 0.33 | 0.55 |
| 8 | 12/42 | 0.54 | 0.88 |
| 4 | 21/42 | 0.71 | **0.97** |

Lifting half the patients to Moderate requires a tau so small the extreme probes hit
~certainty (0.97–1.0) — you're heating the whole gauge, not separating better. 0 patients
ever reach High. Tau changes confidence but NOT the style skew. → Don't crank it alone;
it manufactures false precision.

**Knob #2 — recenter prototypes onto the real patient cluster: FIXES THE SKEW, cheap.**
- Before: Caretakers 25, GT 12, Strivers 3, Weighers 2.
- After recenter: **Strivers 15, Caretakers 9, Weighers 9, GT 6, Free Spirits 3** — all 5
  styles. The Caretakers/Guided-Trusters skew was an artifact of prototypes calibrated for
  a different population, not a deep problem. ~hours of work.
- But recentering does **not** raise confidence (slightly lowers it — prototypes sit closer
  together). Still ~42/42 Low.

**Bottom line on recalibration:**
- Style skew → cheaply fixable (#2). Kills the "always the same 2 labels" complaint.
- Low confidence → NOT honestly fixable without (a) lying (tiny tau) or (b) an OCEAN scorer
  that genuinely spreads people (#3 — real R&D, uncertain).
- **If our patients truly cluster into one narrow type (self-selection), "Low confidence"
  is the CORRECT, honest output** — there are no real distinctions to show confidently.
  Whether the tight cluster is real or a scorer artifact is, again, what only the
  ground-truth pilot settles.

---

## Proposed experiment to bring: ground-truth pilot (TIPI-10)

The only design that actually tests the boss's self-selection hypothesis AND the engine's
validity, because it introduces the missing ingredient: **ground truth.**

- **Instrument:** TIPI-10 (Ten-Item Personality Inventory) — a validated 10-item Big Five
  self-report, ~1 minute. Public domain. Slots into the existing intake chatbot as 10 extra
  questions, or a one-screen post-visit form.
- **Cohort:** ~50–100 consented patients (opt-in, internal, never shown to them as a
  "personality score"). Run until we have enough completed pairs.
- **Two measurements, two questions:**
  1. **Population distribution of the TIPI-10 scores** → *does our patient population
     genuinely cluster high on Agreeableness/Conscientiousness?* Directly tests
     self-selection, independent of the engine.
  2. **Agreement between the engine's OCEAN (from their visit/intake text) and their
     TIPI-10 self-report** → *is the engine valid on our patients?* (correlation per trait;
     segment agreement). This is the validation we currently lack entirely.
- **Decision rule:** if (1) shows a genuinely narrow population AND (2) shows the engine
  tracks it → "Low confidence" is honest and the feature's value is limited → likely stop or
  keep internal-only. If (2) shows poor agreement → the engine isn't valid here → recalibrate
  #3 or stop. If (1) shows real diversity the engine is flattening → #2 + #3 recalibration is
  worth funding.
- **Cost:** instrument is free; work is the intake change + a small analysis. The gating
  cost is consent/compliance + waiting for N patients — so it's a *pilot to propose*, not a
  thing to run this afternoon.
- **Privacy:** TIPI responses are PHI-adjacent; keep them in the existing perimeter, opt-in,
  internal, never patient-facing.
