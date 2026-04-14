# Zoom OAuth Migration — S2S → Authorization Code Grant

**Date:** 2026-04-14
**Status:** Approved

## Overview

Migrate the Zoom integration from Server-to-Server (S2S) OAuth back to the Authorization Code Grant flow. S2S only works for users within the same Zoom account, making it incompatible with a multi-tenant SaaS product where each paying doctor uses their own Zoom account. With the General App (Authorization Code Grant), any Zoom user can authorize the app and their meetings will trigger webhooks automatically.

---

## Section 1: Database Migration

Create a new forward migration (do not use the existing `b2b524604b33` downgrade — preserve migration history).

**Migration steps:**
1. Delete all existing `doctor_video_integration` rows where `provider = 'zoom'` (doctors will reconnect)
2. Add columns back to `doctor_video_integration`:
   - `access_token TEXT NOT NULL`
   - `refresh_token TEXT NOT NULL`
   - `token_expires_at TIMESTAMPTZ NOT NULL`

**Model update (`app/models/doctor_video_integration.py`):**
```python
access_token: str
refresh_token: str
token_expires_at: datetime
```

---

## Section 2: Configuration

**`app/configuration/zoom.py`:**

Remove:
- `ZOOM_ACCOUNT_ID` — no longer needed without S2S

Add back:
- `ZOOM_REDIRECT_URI` — full URL Zoom redirects to after authorization (e.g. `https://your-domain/external/v1/zoom-oauth-callback`)
- `ZOOM_SUCCESS_REDIRECT` — frontend URL to redirect to after successful connect (e.g. `https://your-domain/settings`)

Keep unchanged:
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`
- `ZOOM_OAUTH_TOKEN_URL` (`https://zoom.us/oauth/token`)
- `ZOOM_SECRET_TOKEN` — still needed for webhook validation

**`app/services/zoom_token.py` — delete entirely.** The S2S app-level token is no longer used. Per-doctor token management moves to `zoom.py`.

---

## Section 3: Token Management (`app/services/zoom.py`)

### `_get_doctor_token(integration: DoctorVideoIntegration, *, force: bool = False) -> str`

Returns a valid access token for the given integration. Algorithm:
1. If `force=False` and `token_expires_at` is more than 5 minutes in the future → return `integration.access_token` as-is
2. Otherwise → call `POST https://zoom.us/oauth/token` with `grant_type=refresh_token` and `refresh_token=integration.refresh_token` using Basic Auth header (`client_id:client_secret`)
3. Update `integration.access_token`, `integration.refresh_token`, `integration.token_expires_at` and save
4. Return new `access_token`

### `_zoom_get(path, *, integration, params=None) -> dict`

Replace current `get_app_token()` usage with `_get_doctor_token(integration)`. On 401 response, call `_get_doctor_token(integration, force=True)` and retry once.

Signature change: `integration: DoctorVideoIntegration` replaces the no-arg form. All callers already have access to the integration object.

### `revoke_token(doctor: Doctor) -> None`

1. Get integration for doctor (assert exists)
2. `POST https://zoom.us/oauth/revoke` with Basic Auth and `token=integration.refresh_token`
3. Log result (do not raise if Zoom returns error — token may already be invalid)
4. Delete `DoctorVideoIntegration` record

### `get_user_by_email` — delete

Not needed in the new flow. The doctor's Zoom user ID is obtained from `GET /users/me` during the OAuth callback.

### Other functions updated

`get_zak_token`, `get_user_display_name`, `get_meeting_join_url`, `get_user_meetings`, `delete_meeting` — all pass `integration` to `_zoom_get` instead of calling `get_app_token()` directly. No logic changes.

---

## Section 4: OAuth Routes (`app/routes/zoom_auth.py`)

Restore this file with two public endpoints, mounted at the external router.

### `GET /external/v1/zoom-oauth-start`

Public endpoint (no auth). Builds and redirects to the Zoom authorization URL:

```
https://zoom.us/oauth/authorize
  ?response_type=code
  &client_id={ZOOM_CLIENT_ID}
  &redirect_uri={ZOOM_REDIRECT_URI}
  &state={clerk_id}
```

`clerk_id` is passed as a query parameter from the frontend: `/external/v1/zoom-oauth-start?state={clerk_id}`.

### `GET /external/v1/zoom-oauth-callback`

Public endpoint (no auth). Zoom redirects here after the doctor authorizes.

Steps:
1. Read `code` and `state` (clerk_id) from query params
2. If `error` param present → log and return 400
3. Exchange `code` for tokens: `POST https://zoom.us/oauth/token` with `grant_type=authorization_code`, `code`, `redirect_uri` using Basic Auth
4. Call `GET https://api.zoom.us/v2/users/me` with the new `access_token` to get `zoom_user_id`
5. Look up doctor by `clerk_id` (from `state`)
6. Upsert `DoctorVideoIntegration` with `provider_user_id=zoom_user_id`, `access_token`, `refresh_token`, `token_expires_at=now+expires_in`
7. Redirect to `ZOOM_SUCCESS_REDIRECT`

### `GET /internal/v1/zoom-auth-url` (in `internal.py`)

Restore this authenticated endpoint. Returns:
```json
{ "url": "https://your-domain/external/v1/zoom-oauth-start?state={doctor.clerk_id}" }
```

The frontend uses this URL to initiate the redirect flow.

### `DELETE /internal/v1/zoom-connect` (in `internal.py`) — update

Replace the current "delete record" logic with a call to `zoom_service.revoke_token(doctor)` which revokes the token on Zoom and deletes the record.

---

## Section 5: Frontend (`web/app/routes/settings.tsx`)

Replace the current `ZoomConnect` component (Dialog + email form) with the simpler redirect-based flow:

**Not connected state:**
- Button "Connect Zoom"
- On click: fetch `GET /internal/v1/zoom-auth-url` → `window.location.href = url`
- No Dialog, no email input, no `useRevalidator`

**Connected state (unchanged):**
- Green dot + "Connected" label
- "Disconnect" button → calls `DELETE /internal/v1/zoom-connect` from generated SDK → success toast → `revalidator.revalidate()` to refresh `zoom_connected` without full page reload

**Imports removed:** `Dialog*`, `DialogTrigger`, `Input`, `zoomConnect` (POST), `useRevalidator`, `zoomFormSchema`

**Imports added:** `getClient` from `~/configuration/clerk` (to get bearer token for the auth-url fetch)

After backend changes, regenerate the TypeScript client (`just _js_generate-openapi`) so `zoomAuthUrl` and updated `zoomDisconnect` are available.

---

## Section 6: Zoom Marketplace Setup (manual)

1. Create a **General App** at marketplace.zoom.us
2. Set **Redirect URL**: `ZOOM_REDIRECT_URI` value
3. Add scopes: `user:read`, `meeting:read`, `meeting:write`, `zak`
4. Enable **Event Subscriptions**: same webhook URL and same 4 events (`meeting.started`, `meeting.participant_joined`, `meeting.participant_left`, `meeting.ended`) — set receiver to "All users in the account" (covers all users who have authorized the app)
5. Update `.env` with new `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_REDIRECT_URI`, `ZOOM_SUCCESS_REDIRECT`
6. Remove `ZOOM_ACCOUNT_ID` from `.env`

---

## Section 7: Unchanged

- `app/services/zoom_webhooks.py` — no changes. Identifies doctors by `host_id` matched against `provider_user_id`, which still exists. The `access_token` check was already removed.
- `app/routes/webhooks.py` — no changes.
- `app/jobs/` — no changes.
- Webhook Redis state management — no changes.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Zoom OAuth error on callback | Log + return 400 with detail |
| Token refresh fails (bad refresh token) | 502 from Zoom API call; doctor sees error, must reconnect |
| `revoke_token` returns error | Log warning, proceed with DB delete (token may already be invalid) |
| Doctor not found in callback state | 400 — state param missing or invalid |
