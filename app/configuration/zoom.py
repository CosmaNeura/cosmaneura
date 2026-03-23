from decouple import config

ZOOM_CLIENT_ID = config("ZOOM_CLIENT_ID", cast=str)
ZOOM_CLIENT_SECRET = config("ZOOM_CLIENT_SECRET", cast=str)
ZOOM_SECRET_TOKEN = config("ZOOM_SECRET_TOKEN", cast=str)

ZOOM_OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_REDIRECT_URI = config("ZOOM_REDIRECT_URI", default="http://localhost:8205/auth/zoom/callback", cast=str)
ZOOM_SUCCESS_REDIRECT = config("ZOOM_SUCCESS_REDIRECT", default="https://cosma.localhost/settings", cast=str)
