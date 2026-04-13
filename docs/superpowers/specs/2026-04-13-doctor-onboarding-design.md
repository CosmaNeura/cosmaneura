# Doctor Onboarding Wizard — Design Spec

**Date:** 2026-04-13
**Status:** Approved

## Problem

New doctors signing into Cosma land on a blank screen with no guidance. They don't know what to set up, why it matters, or what the product does for them. The goal is to give new doctors a focused first-impression experience that gets them fully configured before they enter the main app.

## Scope

A single-route onboarding wizard at `/onboarding` that walks new doctors through 4 setup steps before they can access the main app. All steps are required. Existing settings components are reused.

---

## Backend

### Model Change

Add `onboarding_completed_at: datetime | None` to the `Doctor` model.

- Type: `datetime`, nullable, default `null`
- `null` means the doctor has not completed onboarding
- Set to `now()` when the doctor completes the wizard

Requires a new Alembic migration.

### New Endpoint

`POST /internal/v1/onboarding/complete`

- Auth: standard Clerk session auth (same as other `/internal/v1/` routes)
- Sets `onboarding_completed_at = now()` on the authenticated doctor
- Returns `200 OK`

### Settings Endpoint Update

The existing `GET /internal/v1/settings` response must include:

```json
{ "onboarding_completed": true | false }
```

Derived from whether `onboarding_completed_at` is non-null. The frontend uses this to drive redirect logic.

---

## Frontend

### Route

`web/app/routes/onboarding.tsx`

- Full-screen layout, no sidebar
- `clientLoader` calls `settingsData()` and redirects to `/notes` if `onboarding_completed` is already true
- Local `useState` manages the current step (0–4)

### Steps

| Step | Name | Description |
|------|------|-------------|
| 0 | Welcome | App name, one-line value prop, brief explanation of the 3 setup steps ahead. "Get Started" button. |
| 1 | Biography | Reuses `BiographyInput` from `settings.tsx`. Saves via existing `updateSettingsData`. |
| 2 | Example Notes | Reuses `NoteUpload` from `settings.tsx`. Doctor must upload at least one note to advance. |
| 3 | Zoom | Reuses `ZoomConnect` from `settings.tsx`. Step completes once Zoom is connected. |
| 4 | You're all set | Confirmation screen. Calls `POST /internal/v1/onboarding/complete`. "Go to Cosma" button redirects to `/notes`. |

- No "Back" button — linear required flow
- A step indicator at the top shows progress (e.g. "Step 2 of 4")
- Existing components from `settings.tsx` are imported directly — no duplication

### Redirect Enforcement

**Protected routes** (`/notes`, `/schedule`, `/intake`): `clientLoader` calls `settingsData()` and checks `onboarding_completed`. If false, returns `redirect("/onboarding")`.

**`/onboarding` route**: `clientLoader` checks `onboarding_completed`. If true, returns `redirect("/notes")`.

---

## Edge Cases

### Zoom OAuth Callback

Zoom OAuth redirects away from the app and returns via callback. Since `onboarding_completed_at` is still null after the OAuth dance, the redirect enforcement naturally returns the doctor to `/onboarding`. The wizard resumes at step 3 (Zoom), which will now show as connected.

### External API Users

Doctors using `sk_live_` API keys hit `/external/v1/*` routes. These are session-independent and unaffected by onboarding state.

### Admin Users

Admin users with `DoctorRole.admin` access the app the same way. They will go through onboarding on first sign-in like any other doctor.

---

## What Is Not In Scope

- Resumable onboarding (restart on refresh is acceptable for a short, required flow)
- Skippable steps
- In-app onboarding checklist or re-entry flow (Settings page remains the place to update these after onboarding)
- Email drip or external nudges
