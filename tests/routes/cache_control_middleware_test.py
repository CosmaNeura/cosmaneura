"""Tests for the no-store Cache-Control middleware.

The Zoom Marketplace review flagged that responses with sensitive data
(Meeting Link, Meeting ID) were cacheable. This middleware ensures every
API response carries headers that forbid browser/proxy caching.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app


async def test_healthcheck_has_no_store_cache_headers(aclient):
    """Public healthcheck endpoint must include cache-defeating headers."""
    response = await aclient.get(api_app.url_path_for("healthcheck"))

    assert response.status_code == status.HTTP_200_OK
    cache_control = response.headers["Cache-Control"]
    assert "no-store" in cache_control
    assert "no-cache" in cache_control
    assert "must-revalidate" in cache_control
    assert "max-age=0" in cache_control
    assert "private" in cache_control
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


@pytest.mark.parametrize(
    "path,should_have_headers",
    [
        ("/assets/index-abc123.js", False),
        ("/assets/styles-xyz.css", False),
        ("/internal/v1/schedule", True),
        ("/healthcheck", True),
        ("/", True),
    ],
)
async def test_no_store_dispatch_exempts_assets(path, should_have_headers):
    """The middleware skips header-setting only for /assets/* paths."""
    from app.routes.middleware import _no_store_dispatch

    # Stub Request — we only read .url.path
    request = SimpleNamespace(url=SimpleNamespace(path=path))

    # Stub call_next that returns a fake response with a mutable headers dict
    async def fake_call_next(_):
        return SimpleNamespace(headers={})

    # Stubs intentionally don't match the strict Request/Response types — the
    # middleware only touches .url.path and .headers, which the stubs provide.
    response = await _no_store_dispatch(request, fake_call_next)  # type: ignore[arg-type]

    if should_have_headers:
        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"
    else:
        assert "Cache-Control" not in response.headers
        assert "Pragma" not in response.headers
        assert "Expires" not in response.headers


def test_authenticated_schedule_list_has_no_store_headers(
    authenticated_client: TestClient,
):
    """The sensitive endpoint that triggered the Zoom review must carry the headers."""
    # No need to seed a doctor — even a 200 response with empty meetings is enough
    # to verify headers. authenticated_client already wires the auth override.
    with patch("app.routes.internal.sync_zoom_meetings"):
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


async def test_404_response_has_no_store_headers(aclient):
    """Even error responses must carry the cache-defeating headers.

    Uses an unknown Recall ZAK token — a GET route that explicitly raises 404
    when the token is not found in Redis, reliably bypassing the SPA catch-all.
    """
    response = await aclient.get(
        "/external/v1/recall/zak/this-token-does-not-exist-in-redis"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
