# Zoom OAuth Migration (S2S → Authorization Code Grant) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the S2S Zoom OAuth integration with Authorization Code Grant so any doctor using any Zoom account can connect.

**Architecture:** Per-doctor OAuth tokens (access + refresh + expiry) stored in `doctor_video_integration`. A new public router (`zoom_auth_app`) handles the OAuth start/callback redirect flow. The internal API gains a `GET /zoom-auth-url` endpoint and the disconnect endpoint gains token revocation.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, httpx, hey-api openapi-ts, React Router v7, Clerk auth

---

## File Map

| File | Change |
|------|--------|
| `migrations/versions/2026_04_14_f1e2d3c4b5a6_restore_zoom_oauth_token_columns.py` | Create: restore 3 token columns |
| `app/models/doctor_video_integration.py` | Modify: add `access_token`, `refresh_token`, `token_expires_at` fields |
| `app/configuration/zoom.py` | Modify: remove `ZOOM_ACCOUNT_ID`, add `ZOOM_REDIRECT_URI` + `ZOOM_SUCCESS_REDIRECT` |
| `app/services/zoom_token.py` | Delete: S2S token service no longer needed |
| `tests/services/zoom_token_test.py` | Delete: tests for deleted service |
| `app/services/zoom.py` | Rewrite: per-doctor token management; drop `get_user_by_email` |
| `app/routes/zoom_auth.py` | Create: public OAuth start + callback routes |
| `app/routes/internal.py` | Modify: add `zoom_auth_url`, update `zoom_disconnect`, remove `zoom_connect` POST |
| `app/server.py` | Modify: mount `zoom_auth_app` |
| `tests/routes/zoom_connect_test.py` | Replace: old S2S tests → new OAuth tests |
| `web/app/routes/settings.tsx` | Modify: replace dialog flow with redirect flow |
| `web/openapi.json` | Regenerate: after backend changes |
| `web/client/` | Regenerate: TypeScript client after openapi.json update |

---

### Task 1: Database migration — restore OAuth token columns

**Files:**
- Create: `migrations/versions/2026_04_14_f1e2d3c4b5a6_restore_zoom_oauth_token_columns.py`
- Modify: `app/models/doctor_video_integration.py`

- [ ] **Step 1: Create the migration file**

```python
# migrations/versions/2026_04_14_f1e2d3c4b5a6_restore_zoom_oauth_token_columns.py
"""restore_zoom_oauth_token_columns

Revision ID: f1e2d3c4b5a6
Revises: b2b524604b33
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, None] = 'b2b524604b33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Delete existing zoom rows — doctors will reconnect via Authorization Code Grant.
    op.execute("DELETE FROM doctor_video_integration WHERE provider = 'zoom'")
    # Add per-doctor OAuth token columns (table is now empty so NOT NULL is safe).
    op.add_column('doctor_video_integration',
        sa.Column('access_token', sa.Text(), nullable=False))
    op.add_column('doctor_video_integration',
        sa.Column('refresh_token', sa.Text(), nullable=False))
    op.add_column('doctor_video_integration',
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_column('doctor_video_integration', 'token_expires_at')
    op.drop_column('doctor_video_integration', 'refresh_token')
    op.drop_column('doctor_video_integration', 'access_token')
```

- [ ] **Step 2: Run the migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade b2b524604b33 -> f1e2d3c4b5a6, restore_zoom_oauth_token_columns`

- [ ] **Step 3: Update the model to add the three fields**

```python
# app/models/doctor_video_integration.py
from datetime import datetime
from enum import Enum

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import UniqueConstraint

from app.models.doctor import Doctor


class VideoProvider(str, Enum):
    zoom = "zoom"


class DoctorVideoIntegration(
    BaseModel, TimestampsMixin, TypeIDMixin("dvint"), table=True
):
    __table_args__ = (
        UniqueConstraint("doctor_id", "provider", name="uq_doctor_video_integration_provider"),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    "FK to the doctor who owns this integration"

    provider: VideoProvider
    "Video meeting provider (zoom, google_meet, etc.)"

    provider_user_id: str
    "User ID within the provider (e.g. Zoom host_id)"

    access_token: str
    "OAuth access token"

    refresh_token: str
    "OAuth refresh token, used to renew the access token when it expires"

    token_expires_at: datetime
    "When the current access_token expires"
```

- [ ] **Step 4: Verify the app still imports cleanly**

```bash
uv run python -c "from app.models.doctor_video_integration import DoctorVideoIntegration; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/2026_04_14_f1e2d3c4b5a6_restore_zoom_oauth_token_columns.py app/models/doctor_video_integration.py
git commit -m "feat: restore zoom oauth token columns for per-doctor token management"
```

---

### Task 2: Update Zoom configuration

**Files:**
- Modify: `app/configuration/zoom.py`

- [ ] **Step 1: Replace the configuration file**

```python
# app/configuration/zoom.py
from decouple import config

ZOOM_CLIENT_ID = config("ZOOM_CLIENT_ID", default="", cast=str)
ZOOM_CLIENT_SECRET = config("ZOOM_CLIENT_SECRET", default="", cast=str)
ZOOM_SECRET_TOKEN = config("ZOOM_SECRET_TOKEN", default="", cast=str)
ZOOM_REDIRECT_URI = config("ZOOM_REDIRECT_URI", default="", cast=str)
ZOOM_SUCCESS_REDIRECT = config("ZOOM_SUCCESS_REDIRECT", default="", cast=str)

ZOOM_OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"
```

Note: `ZOOM_ACCOUNT_ID` is removed. Add the two new vars to `.env.local` and `.env.test`:
```
ZOOM_REDIRECT_URI=http://localhost:8205/external/v1/zoom-oauth-callback
ZOOM_SUCCESS_REDIRECT=http://localhost:8207/settings
```

- [ ] **Step 2: Verify import**

```bash
uv run python -c "from app.configuration.zoom import ZOOM_REDIRECT_URI, ZOOM_SUCCESS_REDIRECT; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/configuration/zoom.py
git commit -m "feat: update zoom config for authorization code grant (remove account_id, add redirect URIs)"
```

---

### Task 3: Delete zoom_token.py and its tests

**Files:**
- Delete: `app/services/zoom_token.py`
- Delete: `tests/services/zoom_token_test.py`

- [ ] **Step 1: Delete the S2S token service and its tests**

```bash
rm app/services/zoom_token.py
rm tests/services/zoom_token_test.py
```

- [ ] **Step 2: Verify the deleted module is not imported anywhere (besides zoom.py which we'll fix next)**

```bash
grep -r "zoom_token" app/ tests/ --include="*.py" | grep -v "zoom\.py"
```

Expected: no output (only `app/services/zoom.py` should still reference it, and we'll fix that in Task 4)

- [ ] **Step 3: Commit**

```bash
git add -u app/services/zoom_token.py tests/services/zoom_token_test.py
git commit -m "feat: delete s2s zoom token service (replaced by per-doctor oauth tokens)"
```

---

### Task 4: Rewrite app/services/zoom.py for per-doctor token management

**Files:**
- Modify: `app/services/zoom.py`

- [ ] **Step 1: Write the new zoom.py service**

Replace the entire file with:

```python
# app/services/zoom.py
import base64

import httpx
from datetime import datetime, timedelta, timezone

from app import log
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
)
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider

ZOOM_API_BASE = "https://api.zoom.us/v2"
ZOOM_OAUTH_REVOKE_URL = "https://zoom.us/oauth/revoke"
_TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)


def get_zoom_integration(doctor: Doctor) -> DoctorVideoIntegration | None:
    return DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)


def _basic_auth_header() -> str:
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


def _get_doctor_token(integration: DoctorVideoIntegration, *, force: bool = False) -> str:
    """Return a valid access token for this integration, refreshing if needed.

    Refreshes proactively if the token expires within 5 minutes, or immediately
    when force=True (e.g. after a 401 response).
    """
    now = datetime.now(timezone.utc)

    # token_expires_at may be naive or aware depending on DB driver
    expires_at = integration.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not force and expires_at > now + _TOKEN_EXPIRY_BUFFER:
        return integration.access_token

    with httpx.Client() as client:
        response = client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            data={
                "grant_type": "refresh_token",
                "refresh_token": integration.refresh_token,
            },
        )
    response.raise_for_status()
    data = response.json()

    integration.access_token = data["access_token"]
    integration.refresh_token = data["refresh_token"]
    integration.token_expires_at = now + timedelta(seconds=data["expires_in"])
    integration.save()

    log.info("zoom token refreshed", doctor_id=str(integration.doctor_id))
    return integration.access_token


def _zoom_request(
    method: str,
    path: str,
    *,
    integration: DoctorVideoIntegration,
    params: dict | None = None,
) -> httpx.Response:
    """Make an authenticated Zoom API request, retrying once on 401 with a fresh token."""
    token = _get_doctor_token(integration)

    with httpx.Client() as client:
        response = client.request(
            method,
            f"{ZOOM_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom token rejected, refreshing", path=path)
        token = _get_doctor_token(integration, force=True)
        with httpx.Client() as client:
            response = client.request(
                method,
                f"{ZOOM_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

    return response


def _zoom_get(
    path: str,
    *,
    integration: DoctorVideoIntegration,
    params: dict | None = None,
) -> dict:
    response = _zoom_request("GET", path, integration=integration, params=params)
    response.raise_for_status()
    return response.json()


def revoke_token(doctor: Doctor) -> None:
    """Revoke the doctor's Zoom OAuth token and delete the integration record.

    Errors from Zoom are logged but do not raise — the token may already be invalid.
    """
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    with httpx.Client() as client:
        response = client.post(
            ZOOM_OAUTH_REVOKE_URL,
            headers={"Authorization": _basic_auth_header()},
            data={"token": integration.refresh_token},
        )

    if not response.is_success:
        log.warning(
            "zoom token revoke returned error",
            status=response.status_code,
            body=response.text,
        )
    else:
        log.info("zoom token revoked", doctor_id=str(doctor.id))

    integration.delete()


def get_zak_token(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        f"/users/{integration.provider_user_id}/token",
        integration=integration,
        params={"type": "zak"},
    )
    log.info("zoom zak token obtained", doctor_id=str(doctor.id))
    return data["token"]


def get_user_display_name(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/users/{integration.provider_user_id}", integration=integration)
    return f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or data.get("email", "")


def get_meeting_join_url(doctor: Doctor, meeting_id: str) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/meetings/{meeting_id}", integration=integration)
    return data["join_url"]


def get_user_meetings(doctor: Doctor) -> list[dict]:
    """Fetch upcoming scheduled meetings for the authenticated doctor from Zoom API."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        f"/users/{integration.provider_user_id}/meetings",
        integration=integration,
        params={"type": "upcoming", "page_size": "100"},
    )
    log.info("zoom meetings fetched", doctor_id=str(doctor.id), count=len(data.get("meetings", [])))
    return data.get("meetings", [])


def delete_meeting(doctor: Doctor, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting by ID."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    response = _zoom_request("DELETE", f"/meetings/{zoom_meeting_id}", integration=integration)
    response.raise_for_status()
    log.info("zoom meeting deleted", doctor_id=str(doctor.id), zoom_meeting_id=zoom_meeting_id)
```

- [ ] **Step 2: Verify the module imports cleanly (no zoom_token reference)**

```bash
uv run python -c "from app.services.zoom import get_zak_token, revoke_token, get_zoom_integration; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Run the existing test suite to make sure no regressions**

```bash
uv run pytest tests/ -x -q --ignore=tests/routes/zoom_connect_test.py 2>&1 | tail -20
```

Expected: all tests pass (zoom_connect_test.py is ignored since we'll replace it next)

- [ ] **Step 4: Commit**

```bash
git add app/services/zoom.py
git commit -m "feat: rewrite zoom service with per-doctor oauth token management"
```

---

### Task 5: Create app/routes/zoom_auth.py — public OAuth endpoints

**Files:**
- Create: `app/routes/zoom_auth.py`

- [ ] **Step 1: Create the file**

```python
# app/routes/zoom_auth.py
import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app import log
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
    ZOOM_REDIRECT_URI,
    ZOOM_SUCCESS_REDIRECT,
)
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider

ZOOM_OAUTH_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
ZOOM_API_ME_URL = "https://api.zoom.us/v2/users/me"

zoom_auth_app = APIRouter(prefix="/external/v1", tags=["zoom-auth"])


def _basic_auth_header() -> str:
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


@zoom_auth_app.get("/zoom-oauth-start", name="zoom_oauth_start")
def zoom_oauth_start(state: str) -> RedirectResponse:
    """Public: redirect the doctor's browser to Zoom's authorization page.

    The frontend calls GET /internal/v1/zoom-auth-url to get the full URL
    (which includes the clerk_id as state), then does window.location.href = url.
    """
    params = urlencode({
        "response_type": "code",
        "client_id": ZOOM_CLIENT_ID,
        "redirect_uri": ZOOM_REDIRECT_URI,
        "state": state,
    })
    return RedirectResponse(url=f"{ZOOM_OAUTH_AUTHORIZE_URL}?{params}")


@zoom_auth_app.get("/zoom-oauth-callback", name="zoom_oauth_callback")
def zoom_oauth_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Public: Zoom redirects here after the doctor authorizes the app."""
    if error:
        log.error("zoom oauth error", error=error, state=state)
        raise HTTPException(status_code=400, detail=f"Zoom OAuth error: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code parameter")

    # Exchange authorization code for tokens
    with httpx.Client() as client:
        token_response = client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ZOOM_REDIRECT_URI,
            },
        )

    if not token_response.is_success:
        log.error(
            "zoom token exchange failed",
            status=token_response.status_code,
            body=token_response.text,
        )
        raise HTTPException(status_code=400, detail="Token exchange failed")

    token_data = token_response.json()
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

    # Fetch Zoom user ID
    with httpx.Client() as client:
        me_response = client.get(
            ZOOM_API_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if not me_response.is_success:
        log.error("zoom /users/me request failed", status=me_response.status_code)
        raise HTTPException(status_code=400, detail="Failed to retrieve Zoom user info")

    zoom_user_id = me_response.json()["id"]

    # Look up doctor by clerk_id from state
    doctor = Doctor.get(clerk_id=state)
    if not doctor:
        log.error("doctor not found for zoom oauth state", state=state)
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # Upsert integration
    integration = DoctorVideoIntegration.find_or_initialize_by(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
    )
    integration.provider_user_id = zoom_user_id
    integration.access_token = access_token
    integration.refresh_token = refresh_token
    integration.token_expires_at = token_expires_at
    integration.save()

    log.info("zoom oauth connected", doctor_id=str(doctor.id), zoom_user_id=zoom_user_id)
    return RedirectResponse(url=ZOOM_SUCCESS_REDIRECT)
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
uv run python -c "from app.routes.zoom_auth import zoom_auth_app; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/routes/zoom_auth.py
git commit -m "feat: add public zoom oauth start/callback routes"
```

---

### Task 6: Mount zoom_auth_app in server.py

**Files:**
- Modify: `app/server.py`

- [ ] **Step 1: Add the import and mount**

Add `from app.routes.zoom_auth import zoom_auth_app` to the imports at the top of `app/server.py` (after the existing route imports).

Then add `api_app.include_router(zoom_auth_app)` after `api_app.include_router(webhook_api_app)`.

The relevant section of server.py should become:

```python
from app.routes.api import external_api_app
from app.routes.errors import EarlyResponseException
from app.routes.utils.openapi import simplify_operation_ids
from app.routes.webhooks import webhook_api_app
from app.routes.zoom_auth import zoom_auth_app   # ← add this

# ... (rest of imports unchanged) ...

api_app.include_router(internal_api_app)
api_app.include_router(external_api_app)
api_app.include_router(chat_api_app)
api_app.include_router(webhook_api_app)
api_app.include_router(zoom_auth_app)             # ← add this
```

- [ ] **Step 2: Verify routes are registered**

```bash
uv run python -c "
from app.server import api_app
routes = [r.path for r in api_app.routes]
assert '/external/v1/zoom-oauth-start' in routes, f'missing zoom-oauth-start. routes={routes}'
assert '/external/v1/zoom-oauth-callback' in routes, f'missing zoom-oauth-callback. routes={routes}'
print('ok')
"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/server.py
git commit -m "feat: mount zoom_auth_app (public oauth routes) in server"
```

---

### Task 7: Update internal.py — zoom-auth-url + revoke on disconnect + remove zoom-connect POST

**Files:**
- Modify: `app/routes/internal.py`

- [ ] **Step 1: Add the zoom-auth-url endpoint**

After the `update_settings_data` endpoint (around line 285), add:

```python
class ZoomAuthUrlResponse(BaseModel, extra="forbid"):
    url: str


@internal_api_app.get("/zoom-auth-url", name="zoom_auth_url")
def zoom_auth_url(request: Request) -> ZoomAuthUrlResponse:
    """Return the URL the frontend should redirect to in order to start Zoom OAuth."""
    doctor: Doctor = request.state.doctor
    base = str(request.base_url).rstrip("/")
    url = f"{base}/external/v1/zoom-oauth-start?state={doctor.clerk_id}"
    return ZoomAuthUrlResponse(url=url)
```

- [ ] **Step 2: Update zoom_disconnect to revoke the token**

Replace the current `zoom_disconnect` function body:

```python
@internal_api_app.delete("/zoom-connect", name="zoom_disconnect", status_code=status.HTTP_204_NO_CONTENT)
def zoom_disconnect(request: Request) -> None:
    doctor: Doctor = request.state.doctor

    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    if not integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Zoom integration found")

    zoom_service.revoke_token(doctor)
```

- [ ] **Step 3: Remove the zoom_connect POST endpoint and ZoomConnectRequest model**

Delete these lines from internal.py:

```python
class ZoomConnectRequest(BaseModel, extra="forbid"):
    email: str


@internal_api_app.post("/zoom-connect", name="zoom_connect")
def zoom_connect(request: Request, data: ZoomConnectRequest) -> None:
    doctor: Doctor = request.state.doctor

    zoom_user = zoom_service.get_user_by_email(data.email)
    if not zoom_user:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No Zoom user found with that email in your organization",
        )

    integration = DoctorVideoIntegration.find_or_initialize_by(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
    )
    integration.provider_user_id = zoom_user["id"]
    integration.save()
```

- [ ] **Step 4: Verify the app imports cleanly**

```bash
uv run python -c "from app.routes.internal import internal_api_app; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Verify the new route is registered**

```bash
uv run python -c "
from app.server import api_app
routes = {r.path: r for r in api_app.routes if hasattr(r, 'path')}
assert '/internal/v1/zoom-auth-url' in routes, 'missing zoom-auth-url'
print('ok')
"
```

Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add app/routes/internal.py
git commit -m "feat: add zoom-auth-url endpoint, revoke token on disconnect, remove s2s zoom-connect"
```

---

### Task 8: Replace zoom_connect_test.py with OAuth flow tests

**Files:**
- Modify: `tests/routes/zoom_connect_test.py` (fully replace content)

- [ ] **Step 1: Write the new test file**

```python
# tests/routes/zoom_connect_test.py
"""Tests for the Zoom OAuth Authorization Code Grant flow."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.server import api_app

from tests.routes.utils import get_clerk_dev_user


def _get_or_create_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    return doctor


# ---------------------------------------------------------------------------
# GET /external/v1/zoom-oauth-start
# ---------------------------------------------------------------------------

def test_zoom_oauth_start_redirects(client: TestClient):
    """zoom-oauth-start redirects to Zoom authorization URL with correct params."""
    response = client.get(
        "/external/v1/zoom-oauth-start",
        params={"state": "clerk_test123"},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    location = response.headers["location"]
    assert "zoom.us/oauth/authorize" in location
    assert "response_type=code" in location
    assert "state=clerk_test123" in location


def test_zoom_oauth_start_missing_state(client: TestClient):
    """zoom-oauth-start without state param returns 422."""
    response = client.get("/external/v1/zoom-oauth-start", follow_redirects=False)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# GET /external/v1/zoom-oauth-callback
# ---------------------------------------------------------------------------

def _make_token_response() -> MagicMock:
    mock = MagicMock()
    mock.is_success = True
    mock.json.return_value = {
        "access_token": "access_abc",
        "refresh_token": "refresh_xyz",
        "expires_in": 3600,
    }
    return mock


def _make_me_response(zoom_user_id: str = "zoom_user_001") -> MagicMock:
    mock = MagicMock()
    mock.is_success = True
    mock.json.return_value = {"id": zoom_user_id}
    return mock


def test_zoom_oauth_callback_creates_integration(client: TestClient, global_session):
    """Successful callback creates a DoctorVideoIntegration with tokens."""
    doctor = _get_or_create_doctor()

    mock_http = MagicMock()
    mock_http.__enter__ = lambda s: s
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = _make_token_response()
    mock_http.get.return_value = _make_me_response("zoom_user_001")

    with patch("app.routes.zoom_auth.httpx.Client", return_value=mock_http), \
         patch("app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT", "http://frontend/settings"):
        response = client.get(
            "/external/v1/zoom-oauth-callback",
            params={"code": "auth_code_123", "state": doctor.clerk_id},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert response.headers["location"] == "http://frontend/settings"

    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration is not None
    assert integration.provider_user_id == "zoom_user_001"
    assert integration.access_token == "access_abc"
    assert integration.refresh_token == "refresh_xyz"
    assert integration.token_expires_at is not None


def test_zoom_oauth_callback_upserts_existing(client: TestClient, global_session):
    """Callback with an existing integration updates it rather than creating a duplicate."""
    doctor = _get_or_create_doctor()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="old_zoom_id",
        access_token="old_access",
        refresh_token="old_refresh",
        token_expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ).save()

    mock_http = MagicMock()
    mock_http.__enter__ = lambda s: s
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = _make_token_response()
    mock_http.get.return_value = _make_me_response("new_zoom_id")

    with patch("app.routes.zoom_auth.httpx.Client", return_value=mock_http), \
         patch("app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT", "http://frontend/settings"):
        response = client.get(
            "/external/v1/zoom-oauth-callback",
            params={"code": "auth_code_456", "state": doctor.clerk_id},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    integrations = DoctorVideoIntegration.where(doctor_id=doctor.id, provider=VideoProvider.zoom).all()
    assert len(list(integrations)) == 1
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration.provider_user_id == "new_zoom_id"


def test_zoom_oauth_callback_error_param(client: TestClient):
    """Callback with error param returns 400."""
    response = client.get(
        "/external/v1/zoom-oauth-callback",
        params={"error": "access_denied", "state": "clerk_test123"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "access_denied" in response.json()["detail"]


def test_zoom_oauth_callback_unknown_state(client: TestClient, global_session):
    """Callback with unknown clerk_id in state returns 400."""
    mock_http = MagicMock()
    mock_http.__enter__ = lambda s: s
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.post.return_value = _make_token_response()
    mock_http.get.return_value = _make_me_response()

    with patch("app.routes.zoom_auth.httpx.Client", return_value=mock_http):
        response = client.get(
            "/external/v1/zoom-oauth-callback",
            params={"code": "some_code", "state": "clerk_unknown_xyz"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid state" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /internal/v1/zoom-auth-url
# ---------------------------------------------------------------------------

def test_zoom_auth_url_returns_start_url(authenticated_client: TestClient):
    """zoom-auth-url returns a URL pointing to zoom-oauth-start with clerk_id as state."""
    doctor = _get_or_create_doctor()

    response = authenticated_client.get(api_app.url_path_for("zoom_auth_url"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "url" in data
    assert "/external/v1/zoom-oauth-start" in data["url"]
    assert f"state={doctor.clerk_id}" in data["url"]


# ---------------------------------------------------------------------------
# DELETE /internal/v1/zoom-connect (disconnect)
# ---------------------------------------------------------------------------

def test_zoom_disconnect_revokes_and_deletes(authenticated_client: TestClient, global_session):
    """Disconnecting calls revoke_token on Zoom and removes the integration record."""
    doctor = _get_or_create_doctor()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_abc123",
        access_token="acc",
        refresh_token="ref",
        token_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ).save()

    with patch("app.routes.internal.zoom_service.revoke_token") as mock_revoke:
        response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_revoke.assert_called_once()
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration is None


def test_zoom_disconnect_not_connected(authenticated_client: TestClient, global_session):
    """Disconnecting when not connected returns 404."""
    response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))
    assert response.status_code == status.HTTP_404_NOT_FOUND
```

- [ ] **Step 2: Run the new test file**

```bash
uv run pytest tests/routes/zoom_connect_test.py -v 2>&1 | tail -30
```

Expected: all tests pass

- [ ] **Step 3: Run the full test suite**

```bash
uv run pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/routes/zoom_connect_test.py
git commit -m "test: replace s2s zoom tests with oauth authorization code grant tests"
```

---

### Task 9: Update settings.tsx — replace dialog flow with redirect flow

**Files:**
- Modify: `web/app/routes/settings.tsx`

- [ ] **Step 1: Remove the Dialog imports block**

In `settings.tsx`, delete the entire block:
```typescript
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "~/components/ui/dialog"
```

- [ ] **Step 2: Remove the Input import**

Delete the line:
```typescript
import { Input } from "~/components/ui/input"
```

- [ ] **Step 3: Update the client imports**

Change:
```typescript
import {
  generateSignedExampleNoteUploadUrl,
  settingsData,
  updateSettingsData,
  uploadExampleNote,
  zoomConnect,
  zoomDisconnect,
} from "~/configuration/client"
```

To:
```typescript
import {
  generateSignedExampleNoteUploadUrl,
  settingsData,
  updateSettingsData,
  uploadExampleNote,
  zoomAuthUrl,
  zoomDisconnect,
} from "~/configuration/client"
```

- [ ] **Step 4: Remove zoomFormSchema**

Delete:
```typescript
const zoomFormSchema = z.object({
  email: z.string().email("Please enter a valid email address"),
})
```

- [ ] **Step 5: Replace the ZoomConnect component**

Replace the entire `ZoomConnect` function with:

```typescript
function ZoomConnect({ zoomConnected }: { zoomConnected: boolean }) {
  const revalidator = useRevalidator()
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  async function onConnect() {
    setConnecting(true)
    const { data } = await zoomAuthUrl()
    if (data?.url) {
      window.location.href = data.url
    } else {
      toast({
        title: "Failed to initiate Zoom connection. Please try again.",
        variant: "destructive",
      })
      setConnecting(false)
    }
  }

  async function onDisconnect() {
    setDisconnecting(true)
    const { error } = await zoomDisconnect()
    if (error) {
      toast({
        title: "Failed to disconnect. Please try again.",
        variant: "destructive",
      })
      setDisconnecting(false)
      return
    }
    toast({ title: "Zoom disconnected" })
    setDisconnecting(false)
    revalidator.revalidate()
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>Zoom Integration</h3>
      <p className="text-muted-foreground text-sm">
        Connect your Zoom account so Cosma can automatically join your meetings
        and generate visit notes.
      </p>

      {zoomConnected ? (
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-green-500" />
            <span>Connected</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onDisconnect}
            disabled={disconnecting}
          >
            {disconnecting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Disconnecting...
              </>
            ) : (
              "Disconnect"
            )}
          </Button>
        </div>
      ) : (
        <Button onClick={onConnect} disabled={connecting}>
          {connecting ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : (
            "Connect Zoom"
          )}
        </Button>
      )}
    </section>
  )
}
```

- [ ] **Step 6: Update the SettingsPage usage of ZoomConnect**

Change:
```typescript
<ZoomConnect zoomConnected={loaderData.zoom_connected} email={loaderData.email} />
```

To:
```typescript
<ZoomConnect zoomConnected={loaderData.zoom_connected} />
```

- [ ] **Step 7: Verify no TypeScript errors**

```bash
cd web && npx react-router typegen 2>&1 | tail -10
```

Expected: no errors (the `zoomAuthUrl` import won't resolve until Task 10, so expect a module-not-found error there — that's fine for now)

- [ ] **Step 8: Commit**

```bash
git add web/app/routes/settings.tsx
git commit -m "feat: replace zoom connect dialog with oauth redirect flow in settings"
```

---

### Task 10: Regenerate TypeScript client from updated OpenAPI spec

**Files:**
- Modify: `web/openapi.json`
- Modify: `web/client/` (auto-generated)

- [ ] **Step 1: Start the Python dev server (if not already running)**

In a separate terminal:
```bash
just py_dev
```

Wait for: `Application startup complete.`

- [ ] **Step 2: Export the updated OpenAPI spec**

```bash
curl -s http://localhost:8205/openapi.json > web/openapi.json
```

- [ ] **Step 3: Verify the new endpoints appear in the spec**

```bash
grep -c "zoom-auth-url\|zoom-oauth-start\|zoom-oauth-callback" web/openapi.json
```

Expected: `3` (each endpoint appears once)

Also verify the old `zoom-connect` POST is gone:
```bash
grep "zoom_connect" web/openapi.json
```

Expected: only `zoom_disconnect` remains (the disconnect DELETE), no `zoom_connect` POST

- [ ] **Step 4: Generate the TypeScript client**

```bash
cd web && pnpm run openapi 2>&1 | tail -10
```

Expected: completes without errors, `client/` directory updated

- [ ] **Step 5: Verify zoomAuthUrl is now available in the generated client**

```bash
grep -r "zoomAuthUrl" web/client/ | head -5
```

Expected: at least one match showing the generated function

- [ ] **Step 6: Run the frontend typecheck**

```bash
cd web && npx react-router typegen && npx tsc --noEmit 2>&1 | tail -20
```

Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add web/openapi.json web/client/
git commit -m "feat: regenerate typescript client with zoom oauth authorization code grant endpoints"
```

---

## Self-Review

### Spec coverage check

| Spec section | Tasks |
|---|---|
| Section 1: DB Migration (delete zoom rows, restore 3 columns) | Task 1 |
| Section 2: Config (remove ACCOUNT_ID, add REDIRECT_URI + SUCCESS_REDIRECT) | Task 2 |
| Section 2: Delete zoom_token.py | Task 3 |
| Section 3: _get_doctor_token (proactive + reactive refresh) | Task 4 |
| Section 3: _zoom_get (per-doctor token, 401 retry) | Task 4 |
| Section 3: revoke_token (revoke + delete record, log-don't-raise) | Task 4 |
| Section 3: delete get_user_by_email | Task 3+4 |
| Section 3: Other functions pass integration | Task 4 |
| Section 4: GET /external/v1/zoom-oauth-start | Task 5 |
| Section 4: GET /external/v1/zoom-oauth-callback (exchange, /me, upsert) | Task 5 |
| Section 4: GET /internal/v1/zoom-auth-url | Task 7 |
| Section 4: DELETE /internal/v1/zoom-connect (revoke_token) | Task 7 |
| Section 5: Frontend redirect flow (no dialog) | Task 9 |
| Section 5: Regenerate TS client | Task 10 |
| Section 7: zoom_webhooks.py unchanged | verified (not in any task) |

### Type consistency check

- `DoctorVideoIntegration.access_token: str` — used in `_get_doctor_token` ✓
- `DoctorVideoIntegration.refresh_token: str` — used in `_get_doctor_token` + `revoke_token` ✓
- `DoctorVideoIntegration.token_expires_at: datetime` — compared with `datetime.now(timezone.utc)` ✓
- `_zoom_request` / `_zoom_get` signature: `integration: DoctorVideoIntegration` — all callers have integration ✓
- `revoke_token(doctor: Doctor)` — called from `zoom_disconnect` in internal.py with `request.state.doctor` ✓
- `ZoomAuthUrlResponse.url: str` — matches frontend `data?.url` access pattern ✓
