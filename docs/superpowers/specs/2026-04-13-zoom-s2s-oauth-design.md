# Zoom Server-to-Server OAuth Migration

**Date:** 2026-04-13  
**Status:** Approved

## Overview

Migrate the Zoom integration from a user-level OAuth (authorization code grant) app to a Server-to-Server (S2S) OAuth app. This eliminates per-doctor OAuth tokens and replaces the user authorization flow with a lightweight email-based connect step backed by an app-level access token.

---

## Background

The current integration uses the authorization code grant: each doctor individually authorizes the app, and per-doctor `access_token`, `refresh_token`, and `token_expires_at` are stored in `DoctorVideoIntegration`. The new S2S OAuth app type authenticates at the account level — no user authorization required. All existing connected doctors will be asked to reconnect via the new flow.

---

## Section 1: Authentication & Token Management

### How S2S tokens work

Post to `https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ACCOUNT_ID}` with a Basic Auth header (`client_id:client_secret`). Zoom returns an access token valid for 1 hour. There is no refresh token — a new token is fetched when the current one expires.

### New token service: `app/services/zoom_token.py`

Single public function:

- `get_app_token() -> str` — checks Redis for a cached token under a fixed key. If missing or within 5 minutes of expiry, fetches a new token from Zoom, stores it in Redis with a TTL of 55 minutes, and returns it.

All Zoom API calls use this function instead of reading tokens from `DoctorVideoIntegration`.

### Config changes: `app/configuration/zoom.py`

**Add:**
- `ZOOM_ACCOUNT_ID`

**Remove:**
- `ZOOM_REDIRECT_URI`
- `ZOOM_SUCCESS_REDIRECT`
- `zoom_redirect_uri()` helper
- `zoom_success_redirect()` helper

---

## Section 2: Doctor Connection Flow

### Removing the old flow

- Delete `app/routes/zoom_auth.py` entirely
- Remove `GET /internal/v1/zoom-auth-url` from `app/routes/internal.py`

### New connect endpoint: `POST /internal/v1/zoom-connect`

1. Doctor submits their Zoom email from a modal in settings
2. Backend calls `GET /users/{email}` using the S2S app token
3. **Success:** user exists in the Zoom account → store their `id` as `provider_user_id` in `DoctorVideoIntegration`, return 200
4. **Failure:** user not found → return 422 with message: `"No Zoom user found with that email in your organization"`

### Disconnect endpoint: `DELETE /internal/v1/zoom-connect`

Deletes the doctor's `DoctorVideoIntegration` record. No other side effects.

### Settings endpoint: `GET /internal/v1/settings`

No changes needed. Already returns `zoom_connected` based on whether a `DoctorVideoIntegration` record exists.

---

## Section 3: Database Schema

### Migration

Remove token columns from `DoctorVideoIntegration`:
- Drop `access_token`
- Drop `refresh_token`
- Drop `token_expires_at`

### What remains in `DoctorVideoIntegration`

| Column | Description |
|--------|-------------|
| `id` | Primary key |
| `doctor_id` | FK to doctors |
| `provider` | Enum: `zoom`, ... |
| `provider_user_id` | Doctor's Zoom user ID |

The unique constraint `(doctor_id, provider)` is unchanged.

### Data migration

All existing Zoom integration rows are deleted. All doctors must reconnect via the new flow. This avoids stale `provider_user_id` values from the old OAuth flow.

---

## Section 4: Zoom Service Changes

### `app/services/zoom.py`

| Change | Detail |
|--------|--------|
| Remove `refresh_access_token()` | No longer needed |
| Remove `_is_token_expired()` | No longer needed |
| Remove `_basic_auth_header()` | Moves to `zoom_token.py` |
| Update `_zoom_get()` | Replace `integration` token usage with `get_app_token()`; keep `integration` param for `provider_user_id` only |
| Update `delete_meeting()` | Use `get_app_token()` directly |
| `get_zoom_integration()` | Unchanged |

### Files deleted

- `app/routes/zoom_auth.py`

### `app/routes/internal.py`

| Change | Detail |
|--------|--------|
| Remove | `GET /internal/v1/zoom-auth-url` |
| Add | `POST /internal/v1/zoom-connect` |
| Add | `DELETE /internal/v1/zoom-connect` |

### Unchanged files

- `app/services/zoom_webhooks.py` — identifies doctors by `host_id` matched against `provider_user_id`, which still exists
- `app/jobs/sync_zoom_meetings.py` — calls `get_user_meetings(doctor)` which goes through `zoom.py`; internals update transparently

---

## Section 5: Error Handling & Testing

### Error handling

| Scenario | Behavior |
|----------|----------|
| Email not in Zoom account | 422: `"No Zoom user found with that email in your organization"` |
| `get_app_token()` fails | 502: external service failure, same as any other Zoom API failure |
| Doctor has no `DoctorVideoIntegration` | Existing `assert` behavior unchanged — hard fail with clear message |

### Testing

- **`zoom_token.py`:** unit tests for cache hit, cache miss (fetches fresh), near-expiry (fetches fresh)
- **`POST /internal/v1/zoom-connect`:** valid email (200), unknown email (422), Zoom API down (502)
- **`DELETE /internal/v1/zoom-connect`:** success (204), no integration exists (404)
- **`sync_zoom_meetings_test.py`:** mock `get_app_token()` instead of per-doctor tokens; test structure largely unchanged
- **Delete:** any existing tests for `zoom_auth.py` routes

---

## Zoom Marketplace Steps (manual, outside codebase)

1. Create a new **Server-to-Server OAuth** app at marketplace.zoom.us
2. Note the **Account ID**, **Client ID**, and **Client Secret**
3. Enable required scopes:
   - `user:read` — for `/users/{uid}` and `/users/{uid}/token`
   - `meeting:read` — for `/meetings/{id}` and `/users/{uid}/meetings`
   - `meeting:write` — for `DELETE /meetings/{id}`
   - `zak` — for ZAK tokens used by the Recall bot
4. Update environment variables in all environments (`ZOOM_ACCOUNT_ID`, new `ZOOM_CLIENT_ID`, new `ZOOM_CLIENT_SECRET`)
