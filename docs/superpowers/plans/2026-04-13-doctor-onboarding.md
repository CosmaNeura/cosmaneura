# Doctor Onboarding Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a required `/onboarding` wizard that guides new doctors through biography, note uploads, and Zoom setup before they can access the main app.

**Architecture:** A `onboarding_completed_at` timestamp on the `Doctor` model drives all redirect logic. The backend exposes a `POST /internal/v1/onboarding/complete` endpoint. The React Router authenticated layout redirects unboarded doctors to `/onboarding`; the wizard marks completion on the "You're all set" step.

**Tech Stack:** Python/FastAPI (backend), SQLAlchemy/Alembic (DB), React Router v7 / TypeScript / Tailwind (frontend), hey-api OpenAPI client (auto-generated TS), Clerk (auth)

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `app/models/doctor.py` | Add `onboarding_completed_at` field |
| Create | `migrations/versions/YYYY_MM_DD_<hash>_add_onboarding_completed_at.py` | DB migration (auto-generated) |
| Modify | `app/routes/internal.py` | Add `onboarding_completed` to `SettingsData`; add `POST /onboarding/complete` endpoint |
| Create | `tests/routes/onboarding_test.py` | Tests for the new endpoint |
| Modify | `web/app/routes.ts` | Add `/onboarding` route outside authenticated layout |
| Run | `just js_generate-openapi` | Regenerate TS client + React Router types |
| Create | `web/app/routes/onboarding.tsx` | Full wizard component |
| Modify | `web/app/layouts/authenticated.tsx` | Add `clientLoader` that redirects unboarded doctors |

---

## Task 1: Add `onboarding_completed_at` to Doctor model

**Files:**
- Modify: `app/models/doctor.py`

- [ ] **Step 1: Add the field**

  Open `app/models/doctor.py`. Add `datetime` to the import and add `onboarding_completed_at` after the `biography` field:

  ```python
  from datetime import datetime
  ```

  (The file already imports `datetime` — verify and skip if present.)

  Add field to the `Doctor` class after `biography`:

  ```python
  onboarding_completed_at: datetime | None = Field(
      default=None,
      sa_type=sa.DateTime(timezone=True),  # type: ignore
  )
  "timestamp when the doctor completed the onboarding wizard; null means not yet onboarded"
  ```

- [ ] **Step 2: Verify Python is happy**

  ```bash
  uv run python -c "from app.models.doctor import Doctor; print(Doctor.model_fields.keys())"
  ```

  Expected output includes `onboarding_completed_at`.

- [ ] **Step 3: Commit**

  ```bash
  git add app/models/doctor.py
  git commit -m "feat: add onboarding_completed_at to Doctor model"
  ```

---

## Task 2: Generate and apply migration

**Files:**
- Create: `migrations/versions/YYYY_MM_DD_<hash>_add_onboarding_completed_at.py` (auto-generated)

- [ ] **Step 1: Generate migration**

  ```bash
  just db_generate_migration add_onboarding_completed_at
  ```

  Expected: new file created in `migrations/versions/`.

- [ ] **Step 2: Inspect generated migration**

  Open the generated file. Alembic should have detected the new column. Verify the `upgrade()` function contains something like:

  ```python
  op.add_column('doctor', sa.Column('onboarding_completed_at', sa.DateTime(timezone=True), nullable=True))
  ```

  If the body is empty (Alembic didn't detect the change), add the above manually.

- [ ] **Step 3: Apply migration**

  ```bash
  just db_migrate
  ```

  Expected: `Running upgrade ... -> <revision_id>` with no errors.

- [ ] **Step 4: Commit**

  ```bash
  git add migrations/versions/
  git commit -m "feat: migration for doctor onboarding_completed_at"
  ```

---

## Task 3: Write failing tests for onboarding endpoints

**Files:**
- Create: `tests/routes/onboarding_test.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/routes/onboarding_test.py`:

  ```python
  from fastapi import status
  from fastapi.testclient import TestClient

  from app.models.doctor import Doctor
  from app.server import api_app

  from tests.routes.utils import get_clerk_dev_user


  def _get_or_create_doctor() -> Doctor:
      _, _, clerk_user = get_clerk_dev_user()
      doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
      if not doctor.email:
          doctor.email = clerk_user.email_addresses[0].email_address
          doctor.save()
      return doctor


  def test_complete_onboarding_sets_timestamp(authenticated_client: TestClient):
      "POST /onboarding/complete sets onboarding_completed_at on the doctor"
      doctor = _get_or_create_doctor()
      assert doctor.onboarding_completed_at is None

      response = authenticated_client.post(
          api_app.url_path_for("complete_onboarding"),
      )

      assert response.status_code == status.HTTP_200_OK

      doctor_refreshed = Doctor.get(id=doctor.id)
      assert doctor_refreshed is not None
      assert doctor_refreshed.onboarding_completed_at is not None


  def test_complete_onboarding_is_idempotent(authenticated_client: TestClient):
      "Calling complete_onboarding a second time is safe and returns 200"
      response_1 = authenticated_client.post(api_app.url_path_for("complete_onboarding"))
      response_2 = authenticated_client.post(api_app.url_path_for("complete_onboarding"))

      assert response_1.status_code == status.HTTP_200_OK
      assert response_2.status_code == status.HTTP_200_OK


  def test_settings_data_includes_onboarding_completed(authenticated_client: TestClient):
      "GET /settings response includes onboarding_completed boolean"
      response = authenticated_client.get(api_app.url_path_for("settings_data"))

      assert response.status_code == status.HTTP_200_OK
      data = response.json()
      assert "onboarding_completed" in data
      assert isinstance(data["onboarding_completed"], bool)


  def test_settings_data_onboarding_completed_false_before_completing(authenticated_client: TestClient):
      "onboarding_completed is False when onboarding_completed_at is null"
      doctor = _get_or_create_doctor()
      doctor.onboarding_completed_at = None
      doctor.save()

      response = authenticated_client.get(api_app.url_path_for("settings_data"))

      assert response.status_code == status.HTTP_200_OK
      assert response.json()["onboarding_completed"] is False


  def test_settings_data_onboarding_completed_true_after_completing(authenticated_client: TestClient):
      "onboarding_completed is True after complete_onboarding is called"
      authenticated_client.post(api_app.url_path_for("complete_onboarding"))

      response = authenticated_client.get(api_app.url_path_for("settings_data"))

      assert response.status_code == status.HTTP_200_OK
      assert response.json()["onboarding_completed"] is True
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest tests/routes/onboarding_test.py -v
  ```

  Expected: All 5 tests FAIL — `complete_onboarding` route does not exist yet, `settings_data` does not include `onboarding_completed`.

- [ ] **Step 3: Commit the failing tests**

  ```bash
  git add tests/routes/onboarding_test.py
  git commit -m "test: add failing tests for onboarding endpoints"
  ```

---

## Task 4: Implement onboarding endpoints in the backend

**Files:**
- Modify: `app/routes/internal.py`

- [ ] **Step 1: Update `SettingsData` to include `onboarding_completed`**

  Find the `SettingsData` class in `app/routes/internal.py` (around line 249):

  ```python
  class SettingsData(BaseModel, extra="forbid"):
      doctor_id: str
      notes_schema: list[DoctorNoteSchema]
      biography: str | None
      zoom_connected: bool
  ```

  Replace with:

  ```python
  class SettingsData(BaseModel, extra="forbid"):
      doctor_id: str
      notes_schema: list[DoctorNoteSchema]
      biography: str | None
      zoom_connected: bool
      onboarding_completed: bool
  ```

- [ ] **Step 2: Update `settings_data` endpoint to populate the new field**

  Find the `settings_data` function (around line 257). Update the return statement:

  ```python
  return SettingsData(
      notes_schema=cast(list[DoctorNoteSchema], list(notes_schema)),
      doctor_id=str(doctor.id),
      biography=doctor.biography,
      zoom_connected=zoom_integration is not None,
      onboarding_completed=doctor.onboarding_completed_at is not None,
  )
  ```

- [ ] **Step 3: Add `complete_onboarding` endpoint**

  Add the following after the `update_settings_data` function (around line 284). Also add `datetime` and `timezone` to the imports at the top of the file if not already present (`from datetime import datetime, timedelta, timezone` — check the existing import).

  ```python
  @internal_api_app.post("/onboarding/complete", name="complete_onboarding")
  def complete_onboarding(request: Request) -> None:
      doctor: Doctor = request.state.doctor
      if doctor.onboarding_completed_at is None:
          doctor.onboarding_completed_at = datetime.now(timezone.utc)
          doctor.save()
  ```

- [ ] **Step 4: Run the tests to verify they pass**

  ```bash
  uv run pytest tests/routes/onboarding_test.py -v
  ```

  Expected: All 5 tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

  ```bash
  uv run pytest tests/routes/ -v
  ```

  Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

  ```bash
  git add app/routes/internal.py
  git commit -m "feat: add onboarding_completed to settings and complete_onboarding endpoint"
  ```

---

## Task 5: Register the `/onboarding` route and regenerate TypeScript client

**Files:**
- Modify: `web/app/routes.ts`
- Run: `just js_generate-openapi`

- [ ] **Step 1: Add `/onboarding` route to `routes.ts`**

  Open `web/app/routes.ts`. Add the onboarding route BEFORE the authenticated layout block. The file should look like:

  ```typescript
  import type { RouteConfig } from "@react-router/dev/routes"
  import { index, layout, prefix, route } from "@react-router/dev/routes"

  export default [
    index("routes/index.tsx"),
    route("/onboarding", "routes/onboarding.tsx"),
    layout("layouts/authenticated.tsx", [
      ...prefix("intake", [
        layout("layouts/intake.tsx", [
          index("routes/intake/index.tsx"),
          route("/:id", "routes/intake/detail.tsx"),
        ]),
      ]),
      ...prefix("notes", [
        layout("layouts/notes.tsx", [
          index("routes/notes/index.tsx"),
          route(":id", "routes/notes/detail.tsx"),
        ]),
      ]),
      ...prefix("schedule", [
        layout("layouts/schedule.tsx", [
          index("routes/schedule/index.tsx"),
          route(":zoom_meeting_id", "routes/schedule/detail.tsx"),
        ]),
      ]),
      route("/settings", "routes/settings.tsx"),
    ]),
    // public, unauthenticated, intake chat
    route("chat/intake/:id", "routes/chat/intake.tsx"),
  ] satisfies RouteConfig
  ```

- [ ] **Step 2: Create a minimal stub for `routes/onboarding.tsx`** (so typegen doesn't error)

  Create `web/app/routes/onboarding.tsx` with a stub:

  ```typescript
  export default function OnboardingPage() {
    return null
  }
  ```

- [ ] **Step 3: Regenerate the TypeScript client and route types**

  ```bash
  just js_generate-openapi
  ```

  Expected: No errors. The file `web/client/sdk.gen.ts` should now include `completeOnboarding` and `settingsData` should include `onboarding_completed` in its return type.

- [ ] **Step 4: Verify the client has the new types**

  ```bash
  grep -n "completeOnboarding\|onboarding_completed" web/client/sdk.gen.ts
  ```

  Expected: `completeOnboarding` function and `onboarding_completed` property appear.

- [ ] **Step 5: Commit**

  ```bash
  git add web/app/routes.ts web/app/routes/onboarding.tsx web/client/ docs/api.json
  git commit -m "feat: register onboarding route and regenerate TS client"
  ```

---

## Task 6: Add onboarding redirect to authenticated layout

**Files:**
- Modify: `web/app/layouts/authenticated.tsx`

- [ ] **Step 1: Add `clientLoader` with onboarding redirect**

  Open `web/app/layouts/authenticated.tsx`. Replace the entire file content with:

  ```typescript
  import { redirect, href, Outlet } from "react-router"

  import type { Route } from "./+types/authenticated"

  import AdminBar from "~/components/AdminBar"
  import { settingsData } from "~/configuration/client"

  export async function clientLoader(_: Route.ClientLoaderArgs) {
    const { data } = await settingsData()
    if (data && !data.onboarding_completed) {
      throw redirect(href("/onboarding"))
    }
    return {}
  }

  export default function AuthenticatedLayout() {
    return (
      <div className="flex h-screen flex-col">
        <AdminBar />
        <Outlet />
      </div>
    )
  }
  ```

- [ ] **Step 2: Verify TypeScript compiles**

  ```bash
  cd web && pnpm exec tsc --noEmit
  ```

  Expected: No errors related to `authenticated.tsx`.

- [ ] **Step 3: Commit**

  ```bash
  git add web/app/layouts/authenticated.tsx
  git commit -m "feat: redirect unboarded doctors to /onboarding in authenticated layout"
  ```

---

## Task 7: Implement the onboarding wizard

**Files:**
- Modify: `web/app/routes/onboarding.tsx` (replacing the stub from Task 5)

- [ ] **Step 1: Write the full wizard**

  Replace the stub in `web/app/routes/onboarding.tsx` with the full implementation:

  ```typescript
  import { useState } from "react"
  import { redirect, href } from "react-router"
  import { useForm } from "react-hook-form"
  import { z } from "zod"
  import { zodResolver } from "@hookform/resolvers/zod"
  import { invariant } from "@epic-web/invariant"

  import type { Route } from "./+types/onboarding"

  import { Button } from "~/components/ui/button"
  import { Icons } from "~/components/icons"
  import { ImageDropzone } from "~/components/shared/ImageDropzone"
  import { SimpleComboBox } from "~/components/shared/SimpleComboBox"
  import {
    Form,
    FormControl,
    FormDescription,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
  } from "~/components/ui/form"
  import {
    settingsData,
    updateSettingsData,
    generateSignedExampleNoteUploadUrl,
    uploadExampleNote,
    completeOnboarding,
  } from "~/configuration/client"
  import { uploadFileToAzure } from "~/lib/azure-upload"
  import { getClient } from "~/configuration/clerk"
  import { requireEnv } from "~/utils/environment"
  import { toast } from "~/hooks/use-toast"

  type Step = 0 | 1 | 2 | 3 | 4

  // Determine which step to resume from based on what the doctor has already completed.
  // This is important for the Zoom OAuth redirect case: the doctor leaves the app,
  // authenticates with Zoom, and returns to /onboarding — we should resume at step 3, not 0.
  function getInitialStep(data: Route.ClientLoaderArgs extends never ? never : Awaited<ReturnType<typeof clientLoader>>): Step {
    if (!data.biography) return 0
    if (data.notes_schema.length === 0) return 2
    if (!data.zoom_connected) return 3
    return 4
  }

  export async function clientLoader(_: Route.ClientLoaderArgs) {
    const { data } = await settingsData()
    invariant(data, "settings data failed to load")

    if (data.onboarding_completed) {
      throw redirect(href("/notes"))
    }

    return data
  }

  // ─── Step 0: Welcome ────────────────────────────────────────────────────────

  function WelcomeStep({ onNext }: { onNext: () => void }) {
    return (
      <div className="flex flex-col items-center gap-6 text-center">
        <h1 className="text-3xl font-bold">Welcome to Cosma</h1>
        <p className="text-muted-foreground max-w-md text-base">
          Cosma is your AI medical scribe. It listens to your patient visits,
          generates notes in your style, and handles patient intake — so you can
          focus on care.
        </p>
        <p className="text-muted-foreground max-w-md text-sm">
          To get started, we'll set up three things: your biography, your note
          style, and your Zoom connection.
        </p>
        <Button size="lg" onClick={onNext}>
          Get Started
        </Button>
      </div>
    )
  }

  // ─── Step 1: Biography ──────────────────────────────────────────────────────

  function BiographyStep({
    biography,
    onComplete,
  }: {
    biography: string | null
    onComplete: () => void
  }) {
    const formSchema = z.object({
      biography: z.string().min(1, "Biography is required"),
    })

    const form = useForm({
      resolver: zodResolver(formSchema),
      defaultValues: { biography: biography ?? "" },
    })

    async function onSubmit(values: z.infer<typeof formSchema>) {
      await updateSettingsData({ body: values })
      onComplete()
    }

    return (
      <div className="flex flex-col gap-6">
        <div className="space-y-1">
          <h2 className="text-2xl font-semibold">Tell us about yourself</h2>
          <p className="text-muted-foreground text-sm">
            This helps Cosma personalize your patient intake and note generation.
          </p>
        </div>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="biography"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Biography</FormLabel>
                  <FormControl>
                    <textarea
                      className="w-full rounded-md border p-2"
                      rows={5}
                      placeholder="I provide comprehensive dermatologic care for patients of all ages, focusing on medical, surgical, and cosmetic dermatology..."
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Describe your specialty, focus areas, and approach to care.
                  </FormDescription>
                  <FormMessage />
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
                "Save & Continue"
              )}
            </Button>
          </form>
        </Form>
      </div>
    )
  }

  // ─── Step 2: Example Notes ──────────────────────────────────────────────────

  const PREDEFINED_NOTE_TYPES = [
    "Telehealth SOAP Note",
    "Dermatology Office Visit",
    "Annual Wellness Visit",
    "Psychiatry Follow-up Note",
    "Urgent Care Visit",
    "Post-Op Follow-up",
    "Cardiology Consultation",
    "Pediatric Well Visit",
    "Pain Management Note",
    "Physical Therapy Evaluation",
  ]

  function NotesStep({ onComplete }: { onComplete: () => void }) {
    const formSchema = z.object({ note_type: z.string().min(1) })
    const [file, setFile] = useState<File | null>(null)

    const form = useForm({ resolver: zodResolver(formSchema) })

    async function onSubmit(values: z.infer<typeof formSchema>) {
      invariant(file, "a file must be selected before submitting")

      const { data: sasSignature } = await generateSignedExampleNoteUploadUrl()
      invariant(sasSignature, "SAS signature should exist")

      const { stampedFileName } = await uploadFileToAzure(file, sasSignature)

      await uploadExampleNote({
        query: { file_name: stampedFileName, note_type: values.note_type },
      })

      setFile(null)
      toast({
        title: "Note uploaded",
        description: "Your example note has been received and will be processed shortly.",
      })
      onComplete()
    }

    return (
      <div className="flex flex-col gap-6">
        <div className="space-y-1">
          <h2 className="text-2xl font-semibold">Upload an example note</h2>
          <p className="text-muted-foreground text-sm">
            Upload a high-quality example of a visit note you've written. Cosma
            uses it to learn your style and generate future notes that match your
            format.
          </p>
        </div>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <ImageDropzone onDropFile={setFile}>
              {!file ? (
                <div className="flex items-center gap-4 font-semibold">
                  <p>Upload Example Visit Note</p>
                  <p className="text-muted-foreground text-xs">
                    Click or drag a PDF (under 10MB)
                  </p>
                </div>
              ) : (
                <div className="flex flex-col gap-1 font-semibold">
                  <p>{file.name}</p>
                  <p className="text-muted-foreground text-xs">
                    Press Upload to continue
                  </p>
                </div>
              )}
            </ImageDropzone>
            <FormField
              control={form.control}
              name="note_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Note Type</FormLabel>
                  <FormControl>
                    <SimpleComboBox
                      items={PREDEFINED_NOTE_TYPES.map((label) => ({
                        value: label,
                        label,
                      }))}
                      onSelectedItemChange={(item) =>
                        item && field.onChange(item.label)
                      }
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Select the type of note you're uploading.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={!file || form.formState.isSubmitting}>
              {form.formState.isSubmitting ? (
                <>
                  <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                  Uploading...
                </>
              ) : (
                "Upload & Continue"
              )}
            </Button>
          </form>
        </Form>
      </div>
    )
  }

  // ─── Step 3: Zoom ────────────────────────────────────────────────────────────

  function ZoomStep({ onComplete }: { onComplete: () => void }) {
    const [loading, setLoading] = useState(false)

    async function connectZoom() {
      setLoading(true)
      const clerk = await getClient()
      const token = await clerk!.session!.getToken()
      const base = requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
      const res = await fetch(`${base}/internal/v1/zoom-auth-url`, {
        headers: { Authorization: `Bearer ${token}` },
        credentials: "include",
      })
      const { url } = (await res.json()) as { url: string }
      if (url) window.location.href = url
      setLoading(false)
    }

    return (
      <div className="flex flex-col gap-6">
        <div className="space-y-1">
          <h2 className="text-2xl font-semibold">Connect Zoom</h2>
          <p className="text-muted-foreground text-sm">
            Connect your Zoom account so Cosma can automatically join your
            meetings and generate visit notes after each session.
          </p>
        </div>
        <Button onClick={connectZoom} disabled={loading} className="w-fit">
          {loading ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : (
            "Connect Zoom"
          )}
        </Button>
      </div>
    )
  }

  // ─── Step 4: All Set ─────────────────────────────────────────────────────────

  function AllSetStep() {
    const [loading, setLoading] = useState(false)

    async function handleGoToCosma() {
      setLoading(true)
      await completeOnboarding()
      window.location.href = href("/notes")
    }

    return (
      <div className="flex flex-col items-center gap-6 text-center">
        <h2 className="text-3xl font-bold">You're all set!</h2>
        <p className="text-muted-foreground max-w-md text-base">
          Your biography is saved, your note style is loaded, and Cosma will
          join your Zoom meetings automatically. You're ready to start using
          Cosma.
        </p>
        <Button size="lg" onClick={handleGoToCosma} disabled={loading}>
          {loading ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Loading...
            </>
          ) : (
            "Go to Cosma"
          )}
        </Button>
      </div>
    )
  }

  // ─── Step indicator ──────────────────────────────────────────────────────────

  const STEP_LABELS = ["Welcome", "Biography", "Example Notes", "Zoom", "Done"]

  function StepIndicator({ step }: { step: Step }) {
    if (step === 0 || step === 4) return null

    return (
      <p className="text-muted-foreground text-sm">
        Step {step} of {STEP_LABELS.length - 2}
      </p>
    )
  }

  // ─── Main component ──────────────────────────────────────────────────────────

  export default function OnboardingPage({ loaderData }: Route.ComponentProps) {
    const initialStep = getInitialStep(loaderData)
    const [step, setStep] = useState<Step>(initialStep)

    const advance = () => setStep((s) => (s + 1) as Step)

    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="w-full max-w-lg space-y-6 px-8 py-16">
          <StepIndicator step={step} />

          {step === 0 && <WelcomeStep onNext={advance} />}
          {step === 1 && (
            <BiographyStep biography={loaderData.biography} onComplete={advance} />
          )}
          {step === 2 && <NotesStep onComplete={advance} />}
          {step === 3 && <ZoomStep onComplete={advance} />}
          {step === 4 && <AllSetStep />}
        </div>
      </div>
    )
  }
  ```

- [ ] **Step 2: Fix the `getInitialStep` type — it references the loader return type directly**

  The `getInitialStep` function signature uses a complex type that won't compile. Replace it with a simpler typed version. Find this function and replace it with:

  ```typescript
  function getInitialStep(data: {
    biography: string | null
    notes_schema: unknown[]
    zoom_connected: boolean
  }): Step {
    if (!data.biography) return 0
    if (data.notes_schema.length === 0) return 2
    if (!data.zoom_connected) return 3
    return 4
  }
  ```

- [ ] **Step 3: Verify TypeScript compiles**

  ```bash
  cd web && pnpm exec tsc --noEmit
  ```

  Expected: No errors.

- [ ] **Step 4: Commit**

  ```bash
  git add web/app/routes/onboarding.tsx
  git commit -m "feat: implement doctor onboarding wizard"
  ```

---

## Task 8: Manual smoke test

Before claiming done, run through the flow manually.

- [ ] **Step 1: Start the dev server**

  ```bash
  just dev
  ```

- [ ] **Step 2: Test the redirect enforcement**

  Open the app in a browser. Sign in as a doctor whose `onboarding_completed_at` is null (you can verify/reset via `psql $DATABASE_URL -c "UPDATE doctor SET onboarding_completed_at = NULL WHERE email = 'your@email.com'"`).

  Try navigating to `/notes`, `/schedule`, `/settings` — all should redirect to `/onboarding`.

- [ ] **Step 3: Walk through the wizard**

  1. `/onboarding` shows the Welcome step
  2. Clicking "Get Started" shows the Biography step (Step 1 of 3)
  3. Filling in biography and clicking "Save & Continue" shows the Notes step (Step 2 of 3)
  4. Uploading a note and clicking "Upload & Continue" shows the Zoom step (Step 3 of 3)
  5. Clicking "Connect Zoom" initiates the OAuth flow
  6. After returning from OAuth, the page shows Step 3 with Zoom connected — clicking the completed state advances to "You're all set"
  7. Clicking "Go to Cosma" calls `POST /onboarding/complete` and redirects to `/notes`
  8. After completing, navigating to `/onboarding` immediately redirects to `/notes`

- [ ] **Step 4: Verify a completed doctor skips onboarding**

  After completing, visit `/onboarding` — should redirect to `/notes` immediately.

- [ ] **Step 5: Final commit**

  If any polish fixes were made during smoke test:

  ```bash
  git add -p
  git commit -m "fix: onboarding wizard smoke test fixes"
  ```

---

## Notes for implementer

- **Zoom step advancement**: After the OAuth redirect, the loader re-fetches `settingsData()`. The `getInitialStep` function will return `3` if Zoom is now connected but `onboarding_completed_at` is still null. The `ZoomStep` component does not have a way to auto-advance — the user needs to see confirmation that Zoom connected. You may want to show a "Zoom connected ✓" state in `ZoomStep` when `zoom_connected` is true (passed as a prop from loaderData), and have the step auto-call `onComplete()` via `useEffect` in that case. This is a UX improvement you can add if time allows.

- **TypeScript types**: The `+types/onboarding.ts` and `+types/authenticated.ts` files are auto-generated by `react-router typegen` (run as part of `just js_generate-openapi`). If you get "cannot find module" errors, run `just js_generate-openapi` again.

- **Testing**: There are no frontend tests to write — this codebase does not have React component tests. The backend tests in Task 3 cover the API layer.
