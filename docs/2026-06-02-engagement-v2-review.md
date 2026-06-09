# Engagement Engine v2 — review of the spec + code we were sent

**Date:** 2026-06-02 · **Reviewer:** Oscar · **Source:** `PersonalityPredictionStuffV2.zip`
(spec `cosmas-engagement-engine-v2-spec.md` + `cosmas-engagement-engine.zip`, framework 2.0.0).
**Bottom line:** v2 is a genuine improvement and the right direction. Its central promise
— usable confidence where v1 gave 100% Low — **holds on our real transcripts**. But
"confident" is not "valid," the recalibration changes what a role *means*, and the headline
new feature (the gap) can't be tested on current data.

## What v2 is

Not feedback — a full redesign, already decided/implemented (28 offline tests), handed over
to build against. It **drops personality (OCEAN)** and outputs a **3-role communication
style** from the **Control Preferences Scale** (validated, published norms ~26% active /
49% collaborative / 25% passive): **Self-Directed / Collaborative / Directed**, scored on
observable verbal behaviour (decision control, information engagement) + a worry flag.
Adds per-practice recalibration (R3), one ICE intake question, and a preference-enactment
gap (intake=preferred vs visit=enacted).

The diagnosis is **correct and sharp**: v1 collapsed because its 5 styles separated on the
*least observable* traits (A/C/N — Vazire's SOKA model), which a transcript can't carry.
v2 keys on what's actually on the surface of the text. The code matches the spec; rubric
asks for observable behaviour with calibration guidance; offline smoke test distinguishes
directed (dc=20) from self-directed (dc=73) correctly.

## Empirical test — v2 on our own de-identified transcripts (read-only, n=38, enacted channel)

| | v1 (OCEAN) | v2 neutral (day 1) | v2 recalibrated (R3) |
|---|---|---|---|
| mean confidence | 0.19 | **0.471** | 0.456 |
| reach Moderate+ | 0/38 (0%) | **28/38 (74%)** | 29/38 (76%) |
| High / Mod / Low | 0 / 0 / 38 | 4 / 24 / 10 | 4 / 25 / 9 |
| role distribution | collapsed to 2 high-A | Collaborative 74%, Self-Directed 26% | Collaborative 39%, Directed 39%, Self-Directed 21% |

Indicators: decision_control 53.0 ± 12.1 [30–80]; information_engagement 67.8 ± 11.9 [45–95].

**The reframe works, and it isn't cosmetic** — the win comes from observable behaviour + 3
roles on a ~1-D axis, not from lowering tau (22→18, modest). v1 gave 100% Low on these same
transcripts; v2 puts 74% at Moderate+.

## Three things to be deliberate about (do not celebrate blindly)

1. **Confidence ≠ validity — the key risk.** v2 produces confident, spread, plausible roles,
   but there is **no ground truth**: no public clinical corpus labelled with CPS roles (spec
   admits this). We don't know that a patient v2 labels "Self-Directed" actually prefers an
   active role. Showing a clinician a *confident but unvalidated* label is a *bigger* risk
   than v1's honest "Low." → The CPS-administration pilot (our TIPI-10 idea, now CPS) is
   **more** necessary, not less.
2. **R3 recalibration changes what a role MEANS.** Standardizing against the local population
   stretches a compressed distribution: "Directed 39%" means "wants *less* control than the
   typical patient *here*," not "passive in absolute terms." The role becomes relative-to-peers
   — defensible for a communication aid, but must be understood and disclosed. (Caveat: my
   test fit the baseline in-sample, so the 3-way spread is partly self-fulfilling; production
   uses a rolling baseline. Under the **neutral** baseline, **zero** patients read as Directed
   — everyone engages with info in a visit — so the 3-role spread depends entirely on R3.)
3. **The gap feature is untestable today.** It needs the intake/preferred channel; prod has
   9 answered intakes averaging 53 words (see `2026-06-02-engagement-experiments.md`). Until
   the new ICE intake question ships and accrues data, the preference-enactment gap is
   architecture, not a working feature.

## Recommendation

- **Accept the v2 direction** — it clears the bar v1 failed, the diagnosis is right, and the
  CPS grounding is real. Build Phase 1 (vendor + Azure adapter) against it.
- **Make the CPS-administration pilot a gate before clinician-facing rollout**, precisely
  because v2 now shows confident labels. Validate the enacted read against real CPS responses;
  track preference-enactment concordance vs the ~61% literature benchmark.
- **Disclose the relative-to-practice semantics** of the recalibrated role in the UI.
- **Sequence the gap feature after** the ICE intake question ships and data accrues.
- Re-run this empirical check (`run_v2_prod.py`) on a larger sample + a real per-practice
  baseline before trusting the distribution numbers.
