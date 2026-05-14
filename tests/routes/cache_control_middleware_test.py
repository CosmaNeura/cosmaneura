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
    assert "no-store" in response.headers["Cache-Control"]
    assert "no-cache" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
