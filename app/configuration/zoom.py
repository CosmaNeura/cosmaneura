from decouple import config
from fastapi import Request

ZOOM_CLIENT_ID = config("ZOOM_CLIENT_ID", cast=str)
ZOOM_CLIENT_SECRET = config("ZOOM_CLIENT_SECRET", cast=str)
ZOOM_SECRET_TOKEN = config("ZOOM_SECRET_TOKEN", cast=str)

ZOOM_OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_REDIRECT_URI = config("ZOOM_REDIRECT_URI", default="", cast=str)
ZOOM_SUCCESS_REDIRECT = config("ZOOM_SUCCESS_REDIRECT", default="", cast=str)


def zoom_redirect_uri(request: Request) -> str:
	if ZOOM_REDIRECT_URI:
		return ZOOM_REDIRECT_URI

	return str(request.url_for("zoom_oauth_callback"))


def zoom_success_redirect(request: Request) -> str:
	if ZOOM_SUCCESS_REDIRECT:
		return ZOOM_SUCCESS_REDIRECT

	return str(request.base_url.replace(path="/settings", query=""))
