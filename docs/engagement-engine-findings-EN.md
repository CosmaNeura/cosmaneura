# Cosmas Engagement Engine — Findings & Decision Brief

**Date:** 2026-06-01  ·  **Prepared by:** Oscar  ·  **Audience:** Leadership (decision)
**Status:** Technical evaluation complete. Awaiting GO / NO-GO + scope.

---

## 1. What it is

A prototype that reads a patient's **de-identified** intake/visit text, estimates the
**Big Five (OCEAN)** personality traits, and maps them to one of **5 "engagement styles"**
plus a **confidence level**. It is a **communication aid** — meant to help a clinician tailor
*how* they talk to a patient. It is explicitly **not** a diagnosis, a psychological assessment,
or a clinical instrument.

---

## 2. What we evaluated

- **Code & fit:** reviewed the engine's code and how it would plug into our stack and DB.
- **Model swap:** the prototype ships against Anthropic Claude; we re-ran everything on **our
  own Azure OpenAI (gpt-4.1)** — the model we already pay for.
- **Real-data validation:** a **read-only** run over **real de-identified production
  transcripts** (`ai_visit_transcript_non_phi`, the dataset built for exactly this kind of
  analysis), plus an A/B test of one proposed fix.

---

## 3. What we found

### ✅ The code is solid and the model swap works
Clean, typed, tested, honest about its limits. On the public **labeled** benchmark, gpt-4.1
reproduces literature-level accuracy (**~0.56 macro**, range 0.55–0.65 is normal for
text-based Big Five). **We do not need to add Anthropic** — Azure OpenAI is enough.

| Trait | Accuracy (gpt-4.1) |
|---|---|
| Extraversion | 0.66 |
| Conscientiousness | 0.59 |
| Openness | 0.55 |
| Agreeableness | 0.54 |
| Neuroticism | 0.48 |
| **Macro avg** | **0.56** |

### ⚠️ On our real visit transcripts, it is not usable as-is
Over a random sample of real production visits (2,000–9,500 words each):

- **Confidence was "Low" for 100% of patients** — and **not** because text was too short
  (evidence was rated "High" for all). The model rates almost everyone as similarly
  *agreeable / conscientious*, so it cannot cleanly separate the five styles.
- Predictions **skew heavily** toward 2 of the 5 styles (the high-agreeableness ones). A
  clinician would see "Low confidence" + the same couple of labels almost every time.

### ❌ The obvious cheap fix does not work
We hypothesized that feeding **only the patient's words** (dropping the doctor's turns) would
sharpen the signal. We A/B-tested it on the same transcripts:

| Style | All speakers | Patient-only |
|---|---|---|
| Caretakers | 43% | **62%** |
| Guided Trusters | 30% | 24% |
| Strivers | 10% | 3% |
| Weighers | 10% | 7% |
| Free Spirits | 7% | 3% |
| **Confidence** | **100% Low** | **100% Low** |

Patient-only made it **slightly worse** (more concentrated, still 100% Low). **Ruled out by
evidence.**

### Bottom line
The **integration is the easy part** (~1.5–2.5 weeks of engineering). The **value** depends on
real **re-calibration for clinical text**, which is harder and uncertain. One lever remains
untested: using the **intake-chatbot text** (the patient's own words, in a freer register)
instead of the visit transcript — we can check that quickly if desired.

---

## 4. What needs to be decided

| # | Decision | Recommendation |
|---|---|---|
| 1 | **Do we want to infer & show patient personality at all?** (Patient trust / ethics / regulatory exposure.) | Only as an **internal, opt-in pilot** — never patient-facing, never driving clinical decisions. |
| 2 | **Greenlight re-calibration, or stop here?** Wiring it in is cheap; making it *useful* is real R&D with uncertain payoff. | **Don't integrate yet.** Do a short spike: (a) test the intake-text path, (b) re-calibrate for clinical text, then re-validate and decide. |
| 3 | **LLM provider.** | Use **Azure OpenAI** (validated). No Anthropic billing. |
| 4 | **PHI handling.** We'd score the PHI transcript inside our existing pipeline — same exposure profile as today's note generation. | Acceptable, but get a quick **compliance sign-off**. |
| 5 | **Per-provider vs global.** The engine's rubric is global; our standing rule is per-provider customization with no cross-provider migration. | **Global for v1**, revisit later. |

---

## 5. One-liner for the discussion

> *"The model we already pay for can do this, and wiring it in is cheap — but on real visits it
> currently shows 'Low confidence' for every patient, and the cheap fix didn't help.
> Recommendation: a short re-calibration spike before we commit to a full build, not a green
> light to integrate."*

---

*Methodology note: all numbers above come from runs against our Azure OpenAI gpt-4.1 deployment.
The accuracy figures use the public Pennebaker & King essays corpus (human-labeled). The
production findings use a read-only sample of de-identified transcripts. Full data tables and
reproduction scripts are documented in the engineering notes (`2026-06-01-engagement-engine-decision.md`).*
