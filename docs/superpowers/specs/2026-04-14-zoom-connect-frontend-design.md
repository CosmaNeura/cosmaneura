# Zoom Connect Frontend — S2S OAuth Flow

**Date:** 2026-04-14
**Status:** Approved

## Overview

Update the `ZoomConnect` component in `web/app/routes/settings.tsx` to replace the old OAuth redirect flow with the new email-based S2S OAuth connect/disconnect flow. The backend endpoints (`POST /internal/v1/zoom-connect`, `DELETE /internal/v1/zoom-connect`) already exist and are reflected in `web/openapi.json`, but the TypeScript client has not been regenerated yet and the frontend component still calls the removed `/internal/v1/zoom-auth-url` endpoint.

---

## Section 1: Client Code Generation

Run `pnpm openapi` inside `web/` to regenerate `web/client/sdk.gen.ts` from `web/openapi.json`. This produces typed functions:

- `zoomConnect({ body: { email: string } })` → `POST /internal/v1/zoom-connect`
- `zoomDisconnect()` → `DELETE /internal/v1/zoom-connect`

These are imported via `~/configuration/client` (which re-exports everything from `client/sdk.gen`), consistent with how all other API calls are made in the app.

---

## Section 2: Component Design

The `ZoomConnect` component in `settings.tsx` (lines 330–373) is replaced entirely. No new files are created — all changes stay in `settings.tsx`.

**Not connected state:**
- Button "Connect Zoom" opens a Radix `Dialog` (already available at `~/components/ui/dialog`)
- Dialog contains a single email input built with `react-hook-form` + `zod` (`z.string().email()`)
- Submit button shows spinner while request is in flight

**Connected state:**
- Green dot + "Connected" label
- Button "Disconnect" — calls `zoomDisconnect()` immediately (no confirmation dialog, since reconnecting is trivial)
- Disconnect button shows spinner while request is in flight

**Additional imports needed in `settings.tsx`:**
- `Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription` from `~/components/ui/dialog`
- `Input` from `~/components/ui/input`
- `useRevalidator` from `react-router`
- `zoomConnect, zoomDisconnect` from `~/configuration/client`

---

## Section 3: Data Flow & State

```
[Connect Zoom clicked]
  → dialog opens (local state: open=true)

[Email submitted]
  → setLoading(true)
  → zoomConnect({ body: { email } })
  → success: toast("Zoom connected") + dialog closes + revalidator.revalidate()
  → 422 error: toast("No Zoom user found with that email in your organization")
  → setLoading(false)

[Disconnect clicked]
  → setDisconnecting(true)
  → zoomDisconnect()
  → toast("Zoom disconnected") + revalidator.revalidate()
  → setDisconnecting(false)
```

`useRevalidator().revalidate()` re-runs the `clientLoader`, which fetches fresh `zoom_connected` from `GET /internal/v1/settings`. No page reload required.

---

## Section 4: Error Handling

| Scenario | Behavior |
|---|---|
| Email not found in Zoom account (422) | Toast: "No Zoom user found with that email in your organization" — dialog stays open |
| Network/server error on connect | Toast: "Failed to connect Zoom. Please try again." — dialog stays open |
| Network/server error on disconnect | Toast: "Failed to disconnect. Please try again." |

Errors are caught with a `try/catch` around the API call. The form stays open on error so the doctor can correct their email without re-opening the dialog.

---

## Section 5: Validation

- Email field: `z.string().email("Please enter a valid email address")` — client-side validation before the request is sent
- Submit button disabled while loading or while form is invalid

---

## Out of Scope

- No changes to the backend
- No changes to other routes or components
- No confirmation dialog for disconnect
