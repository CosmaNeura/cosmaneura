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
        cached: bytes | None = redis.get(REDIS_TOKEN_KEY)  # type: ignore[assignment]
        if cached:
            return cached.decode()

    token = _fetch_token()
    redis.set(REDIS_TOKEN_KEY, token, ex=REDIS_TOKEN_TTL_SECONDS)
    return token
