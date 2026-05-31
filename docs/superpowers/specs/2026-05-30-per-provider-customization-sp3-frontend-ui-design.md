# SP3 — Frontend: provider-facing per-note-type customization editor

**Date:** 2026-05-30
**Depends on:** SP2 (the customization API)
**See also:** `2026-05-30-per-provider-customization-overview.md`

## Goal

Let a provider edit, on the platform, the configurable note-generation behaviors for
**each of their note types** — structured controls for the known presets plus a
free-text box — without us touching prompts on their behalf.

## Where it lives

The settings page (`web/app/routes/settings.tsx`) already renders the provider's
note schemas (`SettingsData.notes_schema`). Add a **customization section per note
type** there: each note-type already shown gets an expandable "AI output
customization" panel.

If SP2 chose to inline customization in `/settings`, the data is already present;
otherwise fetch per-note-type via the dedicated GET. Follow the existing data-loading
pattern in `settings.tsx` (clientLoader / React Query as used there). Reuse the
generated client (`client/@tanstack/react-query.gen.ts`) and the
`SETTINGS_STALE_TIME`-style caching conventions established in the settings N+1 fix.

## Controls (per note type)

| Preset | Control | Options / labels |
|---|---|---|
| `subjective_source` | radio / select | "Patient statements only" / "Patient + doctor-elicited history" |
| `pronoun_style` | radio / select | "Infer pronouns from the visit" / "Always 'the patient'" |
| `plan_ordering` | radio / select | "Patient actions first, then doctor actions" / "As discussed" |
| `flag_new_medications` | switch | on/off — "Tag newly-prescribed meds '(new this visit)'" |
| `require_plan_reason` | switch | on/off — "Require a reason on every Plan bullet" |
| `ros_distinct_from_subjective` | switch | on/off — "Keep Review of Systems distinct from Subjective" |
| `custom_instructions` | textarea | free text; show remaining-chars counter against the SP2 limit |

Use the existing UI kit (`@/components/ui/*` — switch, select, radio-group,
textarea, button) for consistency.

## Interaction

- Defaults come from the API (always concrete values), so every control renders in a
  defined state even before the provider has customized anything.
- A single **Save** per note-type issues `PUT` via a React Query mutation; on success
  invalidate the settings/customization query so the cache reflects the new values.
- Show a success toast on save and a destructive toast on failure (reuse
  `~/hooks/use-toast`, matching `ZoomConnectionAlert`).
- Brief helper copy per control (one line) so providers understand each behavior.
  Make explicit in copy that changes affect **only this note type**.

## Accessibility / UX notes

- Group controls under a labeled fieldset per note type.
- The free-text box should note that instructions are appended to the AI's guidance
  and may conflict with the structured options if contradictory (the structured
  options take precedence; free text is additive guidance).

## Testing

- Integration test in the Playwright suite (`web/`), reusing the Clerk-auth mocking
  established by PR #59: render settings, toggle a control, save, assert the `PUT`
  fires with the expected body and the success toast appears. Use exact-match text
  assertions (lesson from the `Upload complete` aria-live duplicate, PR #67).
- Component-level vitest for the panel's default-state rendering and the
  char-counter behavior.

## Out of scope

- No per-field instruction editing UI here (that already exists implicitly via
  example-note upload / `DoctorNoteSchema.fields`); this panel is for the
  cross-cutting behaviors only.
- No bulk "apply to all note types" action — that would reintroduce the very
  auto-migration the boss rejected. Each note type is configured independently.
