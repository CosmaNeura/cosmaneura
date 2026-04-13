# Zoom Server-to-Server OAuth Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-doctor OAuth token storage with a single app-level S2S token cached in Redis, and replace the OAuth redirect flow with an email-based connect endpoint.

**Architecture:** A new `zoom_token.py` service manages the app-level token (account credentials grant, Redis-cached for 55 minutes). `zoom.py` drops all user-token logic and calls `get_app_token()` instead. Two new endpoints (`POST /internal/v1/zoom-connect`, `DELETE /internal/v1/zoom-connect`) replace the old OAuth auth-url/callback flow.

**Tech Stack:** FastAPI, SQLModel/ActiveModel, Alembic, Redis, httpx, pytest

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `app/services/zoom_token.py` | App-level S2S token: fetch from Zoom, cache in Redis |
| Create | `tests/services/zoom_token_test.py` | Unit tests for token caching |
| Create | `tests/routes/zoom_connect_test.py` | Route tests for connect/disconnect endpoints |
| Create | `migrations/versions/2026_04_13_XXX_remove_zoom_oauth_token_columns.py` | Drop token columns, delete stale rows |
| Modify | `app/configuration/zoom.py` | Add `ZOOM_ACCOUNT_ID`, remove redirect helpers |
| Modify | `app/models/doctor_video_integration.py` | Remove token fields |
| Modify | `app/services/zoom.py` | Use `get_app_token()`, remove token refresh logic, add `get_user_by_email()` |
| Modify | `app/routes/internal.py` | Remove zoom-auth-url, add zoom-connect/disconnect |
| Modify | `app/server.py` | Remove `zoom_auth_app` import and registration |
| Modify | `tests/job/sync_zoom_meetings_test.py` | Fix `_create_zoom_integration` helper |
| Modify | `tests/routes/schedule_test.py` | Fix `_create_zoom_integration` helper |
| Modify | `.env.shared` | Add `ZOOM_ACCOUNT_ID` env var |
| Delete | `app/routes/zoom_auth.py` | OAuth redirect routes — no longer needed |

---

## Task 1: Write and run the database migration

**Files:**
- Create: `migrations/versions/2026_04_13_XXX_remove_zoom_oauth_token_columns.py`

- [ ] **Step 1: Generate a blank migration revision**

```bash
alembic revision -m "remove_zoom_oauth_token_columns"
```

Expected: a new file in `migrations/versions/` — note the generated filename (it will have a hash ID in it).

- [ ] **Step 2: Fill in the migration**

Open the generated file and replace the `upgrade()` and `downgrade()` functions with:

```python
def upgrade() -> None:
    # Delete all existing Zoom integration rows so doctors reconnect via the new flow.
    op.execute("DELETE FROM doctor_video_integration WHERE provider = 'zoom'")

    # Drop the per-user OAuth token columns — no longer needed with S2S OAuth.
    op.drop_column('doctor_video_integration', 'access_token')
    op.drop_column('doctor_video_integration', 'refresh_token')
    op.drop_column('doctor_video_integration', 'token_expires_at')


def downgrade() -> None:
    op.add_column('doctor_video_integration',
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('doctor_video_integration',
        sa.Column('refresh_token', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('doctor_video_integration',
        sa.Column('access_token', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
```

Also make sure the file imports `sa` and `sqlmodel` at the top — the generated file will have them.

- [ ] **Step 3: Run the migration**

```bash
alembic upgrade head
```

Expected output: `Running upgrade <prev_hash> -> <new_hash>, remove_zoom_oauth_token_columns`

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/
git commit -m "feat: migration to remove zoom oauth token columns"
```

---

## Task 2: Update DoctorVideoIntegration model

**Files:**
- Modify: `app/models/doctor_video_integration.py`

- [ ] **Step 1: Remove token fields from the model**

Replace the entire file content with:

```python
from enum import Enum

import sqlalchemy as sa
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
```

- [ ] **Step 2: Verify no import errors**

```bash
python -c "from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/models/doctor_video_integration.py
git commit -m "feat: remove oauth token fields from DoctorVideoIntegration"
```

---

## Task 3: Fix existing test helpers

The `_create_zoom_integration` helpers in two test files pass token fields that no longer exist on the model. Fix them both before running any other tests.

**Files:**
- Modify: `tests/job/sync_zoom_meetings_test.py`
- Modify: `tests/routes/schedule_test.py`

- [ ] **Step 1: Update `tests/job/sync_zoom_meetings_test.py`**

Replace the `_create_zoom_integration` function:

```python
def _create_zoom_integration(doctor):
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_user_abc",
    ).save()
```

- [ ] **Step 2: Update `tests/routes/schedule_test.py`**

Replace the `_create_zoom_integration` function:

```python
def _create_zoom_integration(doctor):
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_user_abc",
    ).save()
```

- [ ] **Step 3: Run the existing sync job tests to verify they still pass**

```bash
pytest tests/job/sync_zoom_meetings_test.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 4: Run the existing schedule route tests**

```bash
pytest tests/routes/schedule_test.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/job/sync_zoom_meetings_test.py tests/routes/schedule_test.py
git commit -m "fix: update test helpers after removing token fields from DoctorVideoIntegration"
```

---

## Task 4: Update Zoom configuration

**Files:**
- Modify: `app/configuration/zoom.py`
- Modify: `.env.shared`

- [ ] **Step 1: Replace `app/configuration/zoom.py`**

```python
from decouple import config

ZOOM_ACCOUNT_ID = config("ZOOM_ACCOUNT_ID", default="", cast=str)
ZOOM_CLIENT_ID = config("ZOOM_CLIENT_ID", default="", cast=str)
ZOOM_CLIENT_SECRET = config("ZOOM_CLIENT_SECRET", default="", cast=str)
ZOOM_SECRET_TOKEN = config("ZOOM_SECRET_TOKEN", default="", cast=str)

ZOOM_OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"
```

- [ ] **Step 2: Add `ZOOM_ACCOUNT_ID` to `.env.shared`**

In the `# Zoom` section of `.env.shared`, add the new variable directly after the existing ones:

```bash
# Zoom
export ZOOM_CLIENT_ID="${ZOOM_CLIENT_ID:-$(op_read_if_available "op://${OP_DEV_VAULT_UID}/zoom-app-dev/client-id")}"
export ZOOM_CLIENT_SECRET="${ZOOM_CLIENT_SECRET:-$(op_read_if_available "op://${OP_DEV_VAULT_UID}/zoom-app-dev/client-secret")}"
export ZOOM_SECRET_TOKEN="${ZOOM_SECRET_TOKEN:-$(op_read_if_available "op://${OP_DEV_VAULT_UID}/zoom-app-dev/secret-token")}"
export ZOOM_ACCOUNT_ID="${ZOOM_ACCOUNT_ID:-$(op_read_if_available "op://${OP_DEV_VAULT_UID}/zoom-app-dev/account-id")}"
```

Note: the 1Password item path `zoom-app-dev/account-id` assumes the Account ID will be stored there. Adjust to match where you actually store it in 1Password.

- [ ] **Step 3: Verify config imports cleanly**

```bash
python -c "from app.configuration.zoom import ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_SECRET_TOKEN, ZOOM_OAUTH_TOKEN_URL; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/configuration/zoom.py .env.shared
git commit -m "feat: add ZOOM_ACCOUNT_ID, remove redirect uri config"
```

---

## Task 5: Create the S2S token service (TDD)

**Files:**
- Create: `tests/services/zoom_token_test.py`
- Create: `app/services/zoom_token.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/services/zoom_token_test.py`:

```python
from unittest.mock import MagicMock, patch


def test_get_app_token_cache_hit():
    """Returns the cached token from Redis without calling Zoom."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = b"cached_token"

    with patch("app.services.zoom_token.get_redis", return_value=mock_redis), \
         patch("app.services.zoom_token._fetch_token") as mock_fetch:
        from app.services.zoom_token import get_app_token
        result = get_app_token()

    assert result == "cached_token"
    mock_fetch.assert_not_called()


def test_get_app_token_cache_miss():
    """Fetches a fresh token from Zoom when cache is empty and stores it in Redis."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = None

    with patch("app.services.zoom_token.get_redis", return_value=mock_redis), \
         patch("app.services.zoom_token._fetch_token", return_value="new_token"):
        from app.services.zoom_token import get_app_token
        result = get_app_token()

    assert result == "new_token"
    mock_redis.set.assert_called_once_with("zoom:app_token", "new_token", ex=3300)


def test_get_app_token_force_bypasses_cache():
    """force=True skips the Redis cache and always fetches a fresh token."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = b"stale_token"

    with patch("app.services.zoom_token.get_redis", return_value=mock_redis), \
         patch("app.services.zoom_token._fetch_token", return_value="fresh_token"):
        from app.services.zoom_token import get_app_token
        result = get_app_token(force=True)

    assert result == "fresh_token"
    mock_redis.get.assert_not_called()
    mock_redis.set.assert_called_once_with("zoom:app_token", "fresh_token", ex=3300)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/services/zoom_token_test.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.zoom_token'`

- [ ] **Step 3: Create `app/services/zoom_token.py`**

```python
import base64

import httpx

from app import log
from app.configuration.redis import get_redis
from app.configuration.zoom import (
    ZOOM_ACCOUNT_ID,
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
)

REDIS_TOKEN_KEY = "zoom:app_token"
REDIS_TOKEN_TTL_SECONDS = 55 * 60  # 55 min; token is valid for 60


def _basic_auth_header() -> str:
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


def _fetch_token() -> str:
    with httpx.Client() as client:
        response = client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            params={
                "grant_type": "account_credentials",
                "account_id": ZOOM_ACCOUNT_ID,
            },
        )
        response.raise_for_status()
        data = response.json()

    log.info("zoom s2s app token fetched")
    return data["access_token"]


def get_app_token(*, force: bool = False) -> str:
    """Return the cached app-level S2S token, fetching a new one if necessary.

    Set force=True to bypass the cache (e.g. after a 401 response).
    """
    redis = get_redis()

    if not force:
        cached = redis.get(REDIS_TOKEN_KEY)
        if cached:
            return cached.decode()

    token = _fetch_token()
    redis.set(REDIS_TOKEN_KEY, token, ex=REDIS_TOKEN_TTL_SECONDS)
    return token
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/services/zoom_token_test.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_token.py tests/services/zoom_token_test.py
git commit -m "feat: add zoom s2s token service with redis caching"
```

---

## Task 6: Update the Zoom API service

**Files:**
- Modify: `app/services/zoom.py`

- [ ] **Step 1: Replace `app/services/zoom.py`**

```python
import httpx

from app import log
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.services.zoom_token import get_app_token

ZOOM_API_BASE = "https://api.zoom.us/v2"


def get_zoom_integration(doctor: Doctor) -> DoctorVideoIntegration | None:
    return DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)


def _zoom_get(path: str, *, params: dict[str, str] | None = None) -> dict:
    token = get_app_token()

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom app token rejected, refreshing", path=path)
        token = get_app_token(force=True)

        with httpx.Client() as client:
            response = client.get(
                f"{ZOOM_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

    response.raise_for_status()
    return response.json()


def get_zak_token(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/users/{integration.provider_user_id}/token", params={"type": "zak"})

    log.info("zoom zak token obtained", doctor_id=str(doctor.id))
    return data["token"]


def get_user_display_name(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/users/{integration.provider_user_id}")
    return f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or data.get("email", "")


def get_meeting_join_url(doctor: Doctor, meeting_id: str) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/meetings/{meeting_id}")
    return data["join_url"]


def get_user_meetings(doctor: Doctor) -> list[dict]:
    """Fetch upcoming scheduled meetings for the authenticated doctor from Zoom API."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        f"/users/{integration.provider_user_id}/meetings",
        params={"type": "upcoming", "page_size": "100"},
    )

    log.info("zoom meetings fetched", doctor_id=str(doctor.id), count=len(data.get("meetings", [])))
    return data.get("meetings", [])


def get_user_by_email(email: str) -> dict | None:
    """Look up a Zoom user by email in this account. Returns None if not found."""
    token = get_app_token()

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}/users/{email}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.NOT_FOUND:
        return None

    response.raise_for_status()
    return response.json()


def delete_meeting(doctor: Doctor, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting by ID."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    token = get_app_token()

    with httpx.Client() as client:
        response = client.delete(
            f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom app token rejected, refreshing", meeting_id=zoom_meeting_id)
        token = get_app_token(force=True)

        with httpx.Client() as client:
            response = client.delete(
                f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

    response.raise_for_status()
    log.info("zoom meeting deleted", doctor_id=str(doctor.id), zoom_meeting_id=zoom_meeting_id)
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
python -c "from app.services.zoom import get_zoom_integration, get_user_by_email, delete_meeting; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run the sync job tests**

```bash
pytest tests/job/sync_zoom_meetings_test.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 4: Run the schedule route tests**

```bash
pytest tests/routes/schedule_test.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom.py
git commit -m "feat: update zoom service to use app-level s2s token"
```

---

## Task 7: Add zoom connect/disconnect endpoints (TDD)

**Files:**
- Create: `tests/routes/zoom_connect_test.py`
- Modify: `app/routes/internal.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/routes/zoom_connect_test.py`:

```python
from unittest.mock import patch

from fastapi import status
from fastapi.testclient import TestClient

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.server import api_app

from tests.routes.utils import get_clerk_dev_user

CLERK_DEV_USER_EMAIL = "dev@test.com"


def _get_or_create_doctor():
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    if not doctor.email:
        doctor.email = CLERK_DEV_USER_EMAIL
        doctor.save()
    return doctor


def test_zoom_connect_success(authenticated_client: TestClient):
    """Valid Zoom email creates a DoctorVideoIntegration record."""
    doctor = _get_or_create_doctor()

    with patch("app.routes.internal.zoom_service.get_user_by_email", return_value={"id": "zoom_abc123"}):
        response = authenticated_client.post(
            api_app.url_path_for("zoom_connect"),
            json={"email": "doctor@example.com"},
        )

    assert response.status_code == status.HTTP_200_OK
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration is not None
    assert integration.provider_user_id == "zoom_abc123"


def test_zoom_connect_unknown_email(authenticated_client: TestClient):
    """Email not in Zoom account returns 422 with a clear message."""
    with patch("app.routes.internal.zoom_service.get_user_by_email", return_value=None):
        response = authenticated_client.post(
            api_app.url_path_for("zoom_connect"),
            json={"email": "unknown@example.com"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "No Zoom user found" in response.json()["detail"]


def test_zoom_connect_replaces_existing(authenticated_client: TestClient):
    """Connecting when already connected updates the provider_user_id."""
    doctor = _get_or_create_doctor()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="old_zoom_id",
    ).save()

    with patch("app.routes.internal.zoom_service.get_user_by_email", return_value={"id": "new_zoom_id"}):
        response = authenticated_client.post(
            api_app.url_path_for("zoom_connect"),
            json={"email": "doctor@example.com"},
        )

    assert response.status_code == status.HTTP_200_OK
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration.provider_user_id == "new_zoom_id"


def test_zoom_disconnect_success(authenticated_client: TestClient):
    """Disconnecting removes the DoctorVideoIntegration record."""
    doctor = _get_or_create_doctor()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_abc123",
    ).save()

    response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration is None


def test_zoom_disconnect_not_connected(authenticated_client: TestClient):
    """Disconnecting when not connected returns 404."""
    response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))
    assert response.status_code == status.HTTP_404_NOT_FOUND
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/routes/zoom_connect_test.py -v
```

Expected: `LookupError` — route names `zoom_connect` and `zoom_disconnect` don't exist yet.

- [ ] **Step 3: Update `app/routes/internal.py`**

Remove these imports at the top:
```python
# Remove these lines:
from urllib.parse import urlencode
from ..configuration.zoom import ZOOM_CLIENT_ID, zoom_redirect_uri
ZOOM_OAUTH_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
```

Remove the `ZoomAuthUrlResponse` model and the `zoom_auth_url` endpoint (lines 280–293).

Add these two new models and endpoints anywhere after the existing `SettingsData` section:

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


@internal_api_app.delete("/zoom-connect", name="zoom_disconnect", status_code=status.HTTP_204_NO_CONTENT)
def zoom_disconnect(request: Request) -> None:
    doctor: Doctor = request.state.doctor

    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    if not integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Zoom integration found")

    integration.delete()
```

- [ ] **Step 4: Run the new tests**

```bash
pytest tests/routes/zoom_connect_test.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Run all route tests to check for regressions**

```bash
pytest tests/routes/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/routes/internal.py tests/routes/zoom_connect_test.py
git commit -m "feat: add zoom connect/disconnect endpoints, remove oauth auth-url endpoint"
```

---

## Task 8: Remove zoom_auth.py and clean up server.py

**Files:**
- Delete: `app/routes/zoom_auth.py`
- Modify: `app/server.py`

- [ ] **Step 1: Remove the import and router registration from `app/server.py`**

Remove this line from the imports at the top:
```python
from app.routes.zoom_auth import zoom_auth_app
```

Remove this line from the router registrations:
```python
api_app.include_router(zoom_auth_app)
```

- [ ] **Step 2: Delete `app/routes/zoom_auth.py`**

```bash
rm app/routes/zoom_auth.py
```

- [ ] **Step 3: Verify the server starts cleanly**

```bash
python -c "from app.server import api_app; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/ --ignore=tests/integration -v
```

Expected: all tests pass with no import errors.

- [ ] **Step 5: Commit**

```bash
git add app/server.py
git rm app/routes/zoom_auth.py
git commit -m "feat: remove zoom oauth redirect routes"
```

---

## Post-implementation: Zoom Marketplace setup (manual)

These steps happen outside the codebase on marketplace.zoom.us:

1. Create a new **Server-to-Server OAuth** app
2. Copy the **Account ID**, **Client ID**, and **Client Secret** into your environment (1Password under `zoom-app-dev/account-id`, etc.)
3. Enable scopes: `user:read`, `meeting:read`, `meeting:write`, and the `zak` scope
4. Update staging and production environment variables: `ZOOM_ACCOUNT_ID`, and new values for `ZOOM_CLIENT_ID` and `ZOOM_CLIENT_SECRET` from the new S2S app
5. The `ZOOM_SECRET_TOKEN` for webhook validation remains unchanged
