# SP3 — Per-Note-Type Customization UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** In the settings page, let a provider edit the AI-output customization for each of their note-type schemas (the SP2 presets + free-text), saving via the SP2 `PUT` endpoint.

**Architecture:** A self-contained `NoteCustomizationForm` component (one per note-type) built with react-hook-form + zod, mirroring the existing `BiographyInput` pattern in `settings.tsx` (controlled fields, raw `await` call to the generated client, success toast). A small section maps `loaderData.notes_schema` to its matching `loaderData.note_customizations` entry and renders one form per note-type. No new data fetching — the settings `clientLoader` already returns both arrays (SP2).

**Tech Stack:** React, react-router (clientLoader), react-hook-form, zod, the generated hey-api client, shadcn-style UI kit (`@/components/ui/*`), Vitest + React Testing Library. CI gate: `just js_lint` (Prettier + eslint) and `pnpm typecheck`.

**Spec:** `docs/superpowers/specs/2026-05-30-per-provider-customization-sp3-frontend-ui-design.md`

**Branch:** `feat/note-customization-ui-sp3` (already created off `origin/master`; the generated TS client already contains `updateNoteCustomization`, `NoteTypeCustomizationData`, `UpdateNoteTypeCustomization`, and `note_customizations` on the settings response).

---

## Key types & client (already generated, do not regenerate)

- `~/configuration/client` re-exports the SDK; import `updateNoteCustomization` from it (same module `settings.tsx` imports `updateSettingsData`/`settingsData` from).
- `updateNoteCustomization({ path: { schema_id }, body })` → body is `UpdateNoteTypeCustomization`.
- Enums (string unions): `SubjectiveSource = 'patient_only' | 'patient_and_doctor'`, `PronounStyle = 'infer_from_transcript' | 'neutral'`, `PlanOrdering = 'patient_then_doctor' | 'as_discussed'`.
- `NoteTypeCustomizationData` (from the settings response `note_customizations[]`): `{ doctor_note_schema_id, subjective_source, pronoun_style, plan_ordering, flag_new_medications, require_plan_reason, ros_distinct_from_subjective, custom_instructions }`.
- In `settings.tsx`, `loaderData.notes_schema` (list, each has `id`, `note_type`) and `loaderData.note_customizations` (list of `NoteTypeCustomizationData`). Match by `customization.doctor_note_schema_id === schema.id`.

---

## File Structure

- **Create** `web/app/components/NoteCustomizationForm.tsx` — one note-type's form (presents controls, saves).
- **Create** `web/app/components/NoteCustomizationForm.test.tsx` — Vitest component test.
- **Modify** `web/app/routes/settings.tsx` — add a `NoteCustomization` section that renders one `NoteCustomizationForm` per schema, wired into `SettingsPage`.

Run before pushing: `pnpm --dir web typecheck` and `just js_lint` (or `cd web && pnpm run typecheck`). Auto-fix format with `just js_lint-fix`.

---

## Task 1: `NoteCustomizationForm` component + test

**Files:**
- Create: `web/app/components/NoteCustomizationForm.tsx`
- Test: `web/app/components/NoteCustomizationForm.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// web/app/components/NoteCustomizationForm.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { describe, it, expect, vi, beforeEach } from "vitest"

import { NoteCustomizationForm } from "./NoteCustomizationForm"

// Mock the client module the form calls
const updateNoteCustomization = vi.fn().mockResolvedValue({ data: {} })
vi.mock("~/configuration/client", () => ({
  updateNoteCustomization: (args: unknown) => updateNoteCustomization(args),
}))
// Toast is a side effect; stub it
vi.mock("~/hooks/use-toast", () => ({ toast: vi.fn() }))

const SCHEMA = { id: "doctor_note_schema_abc", note_type: "SOAP" }
const CUSTOMIZATION = {
  doctor_note_schema_id: "doctor_note_schema_abc",
  subjective_source: "patient_only" as const,
  pronoun_style: "infer_from_transcript" as const,
  plan_ordering: "patient_then_doctor" as const,
  flag_new_medications: true,
  require_plan_reason: true,
  ros_distinct_from_subjective: false,
  custom_instructions: "Use metric units.",
}

describe("NoteCustomizationForm", () => {
  beforeEach(() => updateNoteCustomization.mockClear())

  it("renders the note type and the current custom instructions", () => {
    render(
      <NoteCustomizationForm schema={SCHEMA} customization={CUSTOMIZATION} />,
    )
    expect(screen.getByText("SOAP")).toBeInTheDocument()
    expect(
      screen.getByDisplayValue("Use metric units."),
    ).toBeInTheDocument()
  })

  it("submits the schema id in the path and the form values in the body", async () => {
    render(
      <NoteCustomizationForm schema={SCHEMA} customization={CUSTOMIZATION} />,
    )
    fireEvent.click(screen.getByRole("button", { name: /save/i }))

    await waitFor(() => expect(updateNoteCustomization).toHaveBeenCalledTimes(1))
    const arg = updateNoteCustomization.mock.calls[0][0]
    expect(arg.path).toEqual({ schema_id: "doctor_note_schema_abc" })
    expect(arg.body.require_plan_reason).toBe(true)
    expect(arg.body.ros_distinct_from_subjective).toBe(false)
    expect(arg.body.custom_instructions).toBe("Use metric units.")
  })
})
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd web && pnpm exec vitest run app/components/NoteCustomizationForm.test.tsx`
Expected: FAIL — module `./NoteCustomizationForm` not found.

- [ ] **Step 3: Implement the component**

```tsx
// web/app/components/NoteCustomizationForm.tsx
import { useForm } from "react-hook-form"

import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Icons } from "@/components/ui/icons"
import { updateNoteCustomization } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

type SchemaLite = { id: string; note_type: string }

type Customization = {
  doctor_note_schema_id: string
  subjective_source: "patient_only" | "patient_and_doctor"
  pronoun_style: "infer_from_transcript" | "neutral"
  plan_ordering: "patient_then_doctor" | "as_discussed"
  flag_new_medications: boolean
  require_plan_reason: boolean
  ros_distinct_from_subjective: boolean
  custom_instructions: string | null
}

const formSchema = z.object({
  subjective_source: z.enum(["patient_only", "patient_and_doctor"]),
  pronoun_style: z.enum(["infer_from_transcript", "neutral"]),
  plan_ordering: z.enum(["patient_then_doctor", "as_discussed"]),
  flag_new_medications: z.boolean(),
  require_plan_reason: z.boolean(),
  ros_distinct_from_subjective: z.boolean(),
  custom_instructions: z.string().max(4000),
})

type FormValues = z.infer<typeof formSchema>

const SELECTS: {
  name: keyof FormValues
  label: string
  help: string
  options: { value: string; label: string }[]
}[] = [
  {
    name: "subjective_source",
    label: "Subjective source",
    help: "Where the Subjective section draws from.",
    options: [
      { value: "patient_only", label: "Patient statements only" },
      { value: "patient_and_doctor", label: "Patient + doctor-elicited history" },
    ],
  },
  {
    name: "pronoun_style",
    label: "Pronouns",
    help: "How the note refers to the patient.",
    options: [
      { value: "infer_from_transcript", label: "Infer pronouns from the visit" },
      { value: "neutral", label: 'Always "the patient"' },
    ],
  },
  {
    name: "plan_ordering",
    label: "Plan ordering",
    help: "Order of bullets within each condition's plan.",
    options: [
      { value: "patient_then_doctor", label: "Patient actions first, then doctor" },
      { value: "as_discussed", label: "As discussed" },
    ],
  },
]

const SWITCHES: { name: keyof FormValues; label: string }[] = [
  { name: "flag_new_medications", label: 'Tag newly-prescribed meds "(new this visit)"' },
  { name: "require_plan_reason", label: "Require a reason on every Plan bullet" },
  {
    name: "ros_distinct_from_subjective",
    label: "Keep Review of Systems distinct from Subjective",
  },
]

export function NoteCustomizationForm({
  schema,
  customization,
}: {
  schema: SchemaLite
  customization: Customization
}) {
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      subjective_source: customization.subjective_source,
      pronoun_style: customization.pronoun_style,
      plan_ordering: customization.plan_ordering,
      flag_new_medications: customization.flag_new_medications,
      require_plan_reason: customization.require_plan_reason,
      ros_distinct_from_subjective: customization.ros_distinct_from_subjective,
      custom_instructions: customization.custom_instructions ?? "",
    },
  })

  async function onSubmit(values: FormValues) {
    await updateNoteCustomization({
      path: { schema_id: schema.id },
      body: {
        ...values,
        custom_instructions: values.custom_instructions || null,
      },
    })
    toast({
      title: "Customization saved",
      description: `Updated AI output settings for "${schema.note_type}". This affects only this note type.`,
    })
  }

  return (
    <div className="flex flex-col rounded-lg border p-4">
      <div className="font-medium">{schema.note_type}</div>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="mt-3 space-y-4">
          {SELECTS.map((s) => (
            <FormField
              key={s.name}
              control={form.control}
              name={s.name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{s.label}</FormLabel>
                  <Select
                    value={String(field.value)}
                    onValueChange={field.onChange}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {s.options.map((o) => (
                        <SelectItem key={o.value} value={o.value}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription>{s.help}</FormDescription>
                </FormItem>
              )}
            />
          ))}

          {SWITCHES.map((s) => (
            <FormField
              key={s.name}
              control={form.control}
              name={s.name}
              render={({ field }) => (
                <FormItem className="flex items-center justify-between gap-4">
                  <FormLabel className="font-normal">{s.label}</FormLabel>
                  <FormControl>
                    <Switch
                      checked={Boolean(field.value)}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          ))}

          <FormField
            control={form.control}
            name="custom_instructions"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Custom instructions</FormLabel>
                <FormControl>
                  <textarea
                    className="w-full rounded-md border p-2"
                    rows={3}
                    maxLength={4000}
                    placeholder="Additional guidance for this note type (optional)."
                    {...field}
                    value={field.value ?? ""}
                  />
                </FormControl>
                <FormDescription>
                  Appended to the AI's guidance. Affects only this note type.
                </FormDescription>
              </FormItem>
            )}
          />

          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save"
            )}
          </Button>
        </form>
      </Form>
    </div>
  )
}
```

If `@/components/ui/icons` (`Icons.spinner`) isn't the correct path/name, match exactly what `settings.tsx`'s `BiographyInput` imports for its spinner (it uses `Icons.spinner`); reuse that same import.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd web && pnpm exec vitest run app/components/NoteCustomizationForm.test.tsx`
Expected: PASS (2 tests). If the `Select` (radix) doesn't render its value text in happy-dom, the first test asserts on `note_type` + the textarea `getByDisplayValue` which don't depend on the Select portal — keep those assertions; if a Select-specific assertion is flaky in happy-dom, do not assert on the Select's rendered option text.

- [ ] **Step 5: Commit**

```bash
git add web/app/components/NoteCustomizationForm.tsx web/app/components/NoteCustomizationForm.test.tsx
git commit -m "feat(web): NoteCustomizationForm — per-note-type AI output editor"
```

---

## Task 2: Wire the section into the settings page

**Files:**
- Modify: `web/app/routes/settings.tsx`

- [ ] **Step 1: Add a section component + render it**

Add the import near the other component imports in `settings.tsx`:
```tsx
import { NoteCustomizationForm } from "~/components/NoteCustomizationForm"
```

Add a section component (near `NoteSchemaDisplay`):
```tsx
function NoteCustomization({
  noteSchemas,
  customizations,
}: {
  noteSchemas: Route.ComponentProps["loaderData"]["notes_schema"]
  customizations: Route.ComponentProps["loaderData"]["note_customizations"]
}) {
  if (noteSchemas.length === 0) return null

  const bySchema = new Map(
    customizations.map((c) => [c.doctor_note_schema_id, c]),
  )

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>AI Output Customization</h3>
      <p className="text-sm">
        Tune how Cosmas writes each of your note types. Changes affect only the
        note type you edit.
      </p>
      {noteSchemas.map((schema) => {
        const customization = bySchema.get(schema.id)
        if (!customization) return null
        return (
          <NoteCustomizationForm
            key={schema.id}
            schema={schema}
            customization={customization}
          />
        )
      })}
    </section>
  )
}
```

Render it inside `SettingsPage`'s `<InnerScrollableContent>`, after `<NoteUpload .../>`:
```tsx
          <NoteUpload noteSchemas={loaderData.notes_schema} />
          <NoteCustomization
            noteSchemas={loaderData.notes_schema}
            customizations={loaderData.note_customizations}
          />
```

(Use the exact prop names already used by sibling components; `loaderData.note_customizations` exists on the settings response after SP2.)

- [ ] **Step 2: Typecheck**

Run: `cd web && pnpm run typecheck`
Expected: passes (no TS errors). If the `schema`/`customization` element types from `Route.ComponentProps["loaderData"]` don't structurally match `NoteCustomizationForm`'s `SchemaLite`/`Customization` props, adjust `NoteCustomizationForm`'s prop types to reference the generated types (`NoteTypeCustomizationData` from `client/types.gen`) rather than the hand-written aliases — prefer importing the generated types over duplicating them.

- [ ] **Step 3: Run the component test + any settings test**

Run: `cd web && pnpm exec vitest run app/components/NoteCustomizationForm.test.tsx`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/app/routes/settings.tsx
git commit -m "feat(web): render per-note-type customization panels in settings"
```

---

## Task 3: Lint + typecheck gate (CI parity)

- [ ] **Step 1: Format + lint**

Run: `just js_lint-fix` then `just js_lint`
Expected: `js_lint` exits 0 (Prettier + eslint clean). Commit any formatting changes.

- [ ] **Step 2: Typecheck**

Run: `cd web && pnpm run typecheck`
Expected: 0 errors.

- [ ] **Step 3: Full FE test run**

Run: `cd web && pnpm exec vitest run`
Expected: all green (the new test + any pre-existing). If a pre-existing unrelated test is already broken on master, note it but don't fix it here.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A web/
git commit -m "chore(web): lint/format for note customization UI"
```

---

## Done criteria

- Settings page shows an "AI Output Customization" section with one form per note-type, pre-filled from `note_customizations` (defaults when no row).
- Editing controls + Save calls `updateNoteCustomization({ path: { schema_id }, body })` and toasts success; copy makes clear it affects only that note type.
- `just js_lint` exits 0; `pnpm run typecheck` 0 errors; `vitest run` green.

## Notes / gotchas

- This is the FE half of the CI: the gate is `just js_lint` (Prettier + eslint) + `pnpm typecheck`, NOT `py_lint`. Run `just js_lint-fix` before pushing (Prettier import-wrapping bit PR #68).
- No data refetch needed after save: the page uses a route `clientLoader`, and each form holds its own state, so post-save the UI already reflects the saved values (mirrors how `BiographyInput` works). Do not add `useMutation`/`invalidateQueries` — match the existing `BiographyInput` raw-await + toast pattern.
- Prefer importing generated types (`NoteTypeCustomizationData`) over the hand-written `Customization` alias if typecheck complains about structural mismatch with `loaderData`.
- No bulk "apply to all note types" control (would reintroduce the auto-migration the boss rejected).
