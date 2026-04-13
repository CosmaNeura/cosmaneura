from decouple import config

ZOOM_ACCOUNT_ID = config("ZOOM_ACCOUNT_ID", default="", cast=str)
ZOOM_CLIENT_ID = config("ZOOM_CLIENT_ID", default="", cast=str)
ZOOM_CLIENT_SECRET = config("ZOOM_CLIENT_SECRET", default="", cast=str)
ZOOM_SECRET_TOKEN = config("ZOOM_SECRET_TOKEN", default="", cast=str)

ZOOM_OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"
