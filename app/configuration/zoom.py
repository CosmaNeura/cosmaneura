from decouple import config

ZOOM_CLIENT_ID = config("ZOOM_CLIENT_ID", default="", cast=str)
ZOOM_CLIENT_SECRET = config("ZOOM_CLIENT_SECRET", default="", cast=str)
ZOOM_SECRET_TOKEN = config("ZOOM_SECRET_TOKEN", default="", cast=str)
ZOOM_REDIRECT_URI = config("ZOOM_REDIRECT_URI", default="", cast=str)
ZOOM_SUCCESS_REDIRECT = config("ZOOM_SUCCESS_REDIRECT", default="", cast=str)
ZOOM_WATCHDOG_ENABLED = config("ZOOM_WATCHDOG_ENABLED", default=False, cast=bool)

ZOOM_OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"
