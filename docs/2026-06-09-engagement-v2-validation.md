# Engagement Engine v2 — Validation Findings (real prod data)

**Date:** 2026-06-09 · **Prepared by:** Oscar · **Audience:** Eng + leadership
**Status:** Technical validation of v2 complete. Encouraging but not conclusive — see §5.
**Follows:** `engagement-engine-findings-EN.md` (v1 GO/NO-GO) and
`2026-06-01-engagement-engine-decision.md` (v1 engineering notes).

---

## 1. Context — why this run exists

v1 (Big Five → 5 "engagement styles") was rejected on real visit transcripts: it
returned **"Low confidence" for ~100% of patients** and **collapsed onto 2 of the 5
styles** (the high-agreeableness ones). The cheap fix (patient-only turns) didn't help.

v2 is the pivot built in response: it drops personality inference and instead estimates
the patient's **communication / decision-making role**, grounded in the **Control
Preferences Scale (CPS)** — 3 roles (Self-Directed / Collaborative / Directed = CPS
active / collaborative / passive). It adds **R3 per-practice recalibration** to counter
range restriction, and a **preference-enactment gap** (intake vs visit).

This document validates whether v2 actually fixes v1's two fatal flaws, using real
de-identified production transcripts.

---

## 2. Method

- **Data:** random samples from `ai_visit_transcript_non_phi` (2,267 de-identified
  transcripts in prod), **read-only**. We score only the **patient's turns** (the
  enacted voice).
- **Model:** our own **Azure OpenAI `gpt-4.1-2025-04-14`** (the v1-validated swap; no
  Anthropic billing). The production `AzureOpenAILLMClient` was used as-is.
- **Safety:** scripts forced `PYTHON_ENV=development` and never called
  `setup()`/`configure_database()`, so the prod/staging **auto-migration path was never
  reached** — confirmed (no migration logs). Only `SELECT` was issued. No transcript
  text was printed or persisted; only word counts + derived role labels.
- **Limitation baked into the dataset:** non-PHI rows carry **no doctor/appointment FK**,
  so we could **not** test the intake (preferred) channel, the preference-enactment gap,
  or the *real* per-doctor R3 baseline. As a proxy for recalibration we built a
  **pooled, sample-wide baseline** ("sim R3").

Two runs:
1. **Distribution validation** (N=40 → 38 scored) — does v2 fix the confidence/collapse.
2. **Accuracy proxy** (N=40) — double-labeling against an independent read (§4).

---

## 3. Run 1 — Confidence & role separation (does v2 fix v1's flaws?)

### Confidence bands — v1 was ~100% "Low"

| | Low | Moderate | High |
|---|---|---|---|
| **v1 (Big Five)** | ~100% | — | — |
| **v2 neutral (absolute)** | 29% (11) | 66% (25) | 5% (2) |
| **v2 recalibrated (sim R3)** | 39% (15) | 55% (21) | 5% (2) |

✅ **Fixed.** On the absolute scale ~**71% of patients now land Moderate/High** (was 0%).

### Role mix — v1 collapsed onto 2 of 5 styles

| | Distribution |
|---|---|
| **CPS literature norms** | Collaborative 49% · Self-Directed 26% · Directed 25% |
| **v2 neutral (absolute)** | Collaborative **76%** · Self-Directed 24% · Directed **0%** |
| **v2 recalibrated (sim R3)** | Collaborative **53%** · Directed **29%** · Self-Directed **18%** |

⚠️/✅ On the absolute scale v2 still leans Collaborative (76%, zero Directed) — the same
range-restriction symptom as v1, but **less severe** (2 of 3, not 2 of 5, and with usable
confidence). **Recalibration is the strong result:** the mix opens across all 3 roles and
lands almost exactly on the CPS population norms (53/29/18 ≈ 49/25/26).

### Other

- **Mean role separation** (softmax top prob): neutral 0.653 → recal 0.660 (clean; v1 did
  not separate).
- **6/38** transcripts lifted Low → Moderate/High by recalibration.
- ⚠️ **Expressed-concern flag fired for 27/38 (71%)** — the threshold (60) looks too
  sensitive for visit transcripts. **Tuning candidate.**

---

## 4. Run 2 — Accuracy proxy (no patient ground truth yet)

True accuracy needs `CpsSelfReport` ground truth, which is **empty in prod**. As an
interim proxy (the analog of how v1 was checked against the labeled Pennebaker corpus),
we **double-labeled** each transcript: (1) the **engine** pipeline, vs (2) an
**independent** gpt-4.1 read using a *different method* — a holistic, CPS-instrument-style
"which decision-role card fits?" judgment with no 0–100 indicators and no softmax.

| Comparison | Agreement | Cohen's κ |
|---|---|---|
| **engine (neutral)** vs independent | **68%** | 0.365 |
| engine (sim R3) vs independent | 62% | 0.349 |

Reference: chance (3 roles) = 33% · CPS literature concordance benchmark = 61%.

**Confusion** (truth = independent → engine neutral):

| truth ↓ \ engine → | active | collaborative | passive |
|---|---|---|---|
| **active** (13) | **8** | 4 | 1 |
| **collaborative** (23) | 4 | **19** | 0 |
| **passive** (4) | 0 | 4 | **0** |

- Collaborative **83%** (19/23), Active **62%** (8/13), **Passive 0%** (0/4).
- The engine on the neutral baseline is **blind to the passive/Directed role** (labeled
  only 1 transcript passive in the whole sample). Sim-R3 surfaces passive (8 labels) but
  overall agreement drops to 62% — it catches Directed at the cost of noise elsewhere.
- The independent labeler also found few passive (4/40), so this sample may genuinely
  have few Directed patients — indistinguishable from joint under-detection without real
  ground truth.

**Caveats (load-bearing):**
1. Both labels are **gpt-4.1** → **method** independence, not **model** independence;
   shared priors likely **inflate** the 68%.
2. "Truth" here is an LLM's holistic read of the **transcript**, **not** the patient's
   self-reported preference.

---

## 5. Verdict

> **v2 resolves the two reasons v1 was rejected:** confidence is no longer universally
> "Low" (~71% Moderate+), and with recalibration the role mix reproduces CPS population
> norms instead of collapsing. Against an independent holistic CPS read, the engine
> agrees **68%** of the time — above the 61% literature benchmark and ~2× chance (κ=0.365,
> "fair"). **Encouraging, not conclusive.**

**Still open before a definitive GO:**
1. **Real ground truth.** Collect `CpsSelfReport` (CPS-1item) and run the existing
   `compare_engine_vs_self_report` gate. Prefer a **preferred-vs-preferred** comparison
   (score intake text vs self-report), not enacted-vs-preferred.
2. **Passive/Directed blindness.** The neutral baseline never identifies Directed
   patients; real per-doctor R3 (not the pooled proxy) is the intended fix — validate it.
3. **Concern threshold.** 71% flag rate is implausibly high; retune.
4. **Independence.** Re-run the proxy with a **different model family or a human rater**.

---

## 6. Reproduction

Read-only, Azure gpt-4.1, two random samples of N=40 from `ai_visit_transcript_non_phi`.
~16 min each (rate-limited by the Azure deployment, frequent 429 backoffs). The throwaway
scripts were removed after this run; the method is fully described in §2–§4 and the engine
code lives in `app/engagement/`. Per-row labels (no PHI) were captured during the run for
this writeup and then discarded.
