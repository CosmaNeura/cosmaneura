"""Tests for the no-store Cache-Control middleware.

The Zoom Marketplace review flagged that responses with sensitive data
(Meeting Link, Meeting ID) were cacheable. This middleware ensures every
API response carries headers that forbid browser/proxy caching.
"""

from fastapi import status

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


from types import SimpleNamespace

import pytest


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

    response = await _no_store_dispatch(request, fake_call_next)

    if should_have_headers:
        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"
    else:
        assert "Cache-Control" not in response.headers
        assert "Pragma" not in response.headers
        assert "Expires" not in response.headers
