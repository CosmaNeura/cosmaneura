# SP4 — Per-config eval harness

**Date:** 2026-05-30
**Depends on:** SP1 (config-driven prompt). Can run in parallel with SP2/SP3.
**See also:** `2026-05-30-per-provider-customization-overview.md`

## Goal

Make the live-LLM eval harness **configuration-aware** so we can (a) regression-test
that the **default** customization reproduces today's behavior, (b) verify each
non-default preset actually changes output in the intended direction, and (c) prove
**isolation** — changing one note-type's config does not affect another's. This also
operationalizes the boss's "eval set / prompt change regression" clarification:
evals are test-only, never touch production, and are now per-config.

## Background

- `tests/eval.py` holds the judge harness (`eval_llm`, `eval_n_of_m`). The judge
  model is now configurable via `OPENAI_EVAL_MODEL` (shipped 2026-05-30, PR #68) —
  point it at a deployed model on staging where `gpt-4.1-mini-2025-04-14` is absent.
- `tests/job/summarize_test.py` holds the #33 live evals, gated by
  `RUN_LIVE_LLM_TESTS=true` and the presence of Azure OpenAI creds. They currently
  exercise the **default** (global) prompt.

## Work

1. **Helper to generate a note under a given customization.** Add a small test
   helper that, given a transcript fixture, a schema, and a
   `NoteTypeCustomization` (or `None`), calls `generate_clinical_note(...,
   customization=...)` and returns the note. This lets every eval declare the config
   it is testing instead of relying on the global default.

2. **Default-equivalence regression.** A non-LLM unit test (cheap, runs in CI):
   assert the assembled prompt for `customization=None` / `default_customization()`
   matches the golden snapshot from SP1 — i.e. the default behavior is byte-stable.
   This is the primary guard that SP1 didn't silently change anyone's output.

3. **Per-preset behavior evals (live, gated).** For each configurable preset, one
   eval pair on a targeted transcript fixture:
   - default value → criterion that current behavior holds (e.g.
     `require_plan_reason=True` → "every Plan bullet states a reason");
   - flipped value → criterion that the behavior changed (e.g.
     `require_plan_reason=False` → bullets may omit reasons; assert the instruction's
     effect is gone). Reuse the existing fixtures from PRs #60–64 where possible —
     each #33 PR already added a transcript that exercises its behavior.

4. **Isolation test (non-LLM).** Given two `DoctorNoteSchema`s for the same doctor
   with **different** customizations, assert that building the prompt for schema A
   uses A's config and schema B uses B's — and that saving A's customization does not
   mutate B's row or B's assembled prompt. This is the concrete, fast proof of "no
   auto-migration across note types," and needs no LLM.

5. **Docs note.** Add a short comment/README note in `tests/` stating: evals require
   `RUN_LIVE_LLM_TESTS=true` + Azure creds + `OPENAI_EVAL_MODEL` set to a deployed
   model; they are test-only and never affect production note generation.

## Running

```bash
PYTHON_ENV=test \
OPENAI_EVAL_MODEL=gpt-4.1-2025-04-14 \
RUN_LIVE_LLM_TESTS=true \
AZURE_OPENAI_API_KEY=… AZURE_OPENAI_ENDPOINT=… \
uv run pytest tests/job/summarize_test.py -v
```

(Azure creds via 1Password — see the session handoffs; run `op read` calls
sequentially to avoid the biometric-prompt collision.)

## Testing strategy summary

| Test | LLM? | CI? | Proves |
|---|---|---|---|
| Default golden snapshot (SP1) | no | yes | defaults == current behavior |
| Per-preset prompt-flip units (SP1) | no | yes | each toggle changes only its text |
| Isolation (two schemas) | no | yes | no cross-note-type / cross-provider bleed |
| Per-preset behavior evals | yes | no (gated) | the LLM actually honors each config |

The non-LLM tests carry the correctness guarantees in CI; the gated live evals
provide periodic confidence that the model honors the instructions.
