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
