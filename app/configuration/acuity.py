from decouple import config

ACUITY_CLIENT_ID = config("ACUITY_CLIENT_ID", default="", cast=str)
ACUITY_CLIENT_SECRET = config("ACUITY_CLIENT_SECRET", default="", cast=str)
ACUITY_REDIRECT_URI = config("ACUITY_REDIRECT_URI", default="", cast=str)
ACUITY_SUCCESS_REDIRECT = config("ACUITY_SUCCESS_REDIRECT", default="", cast=str)

ACUITY_OAUTH_AUTHORIZE_URL = "https://acuityscheduling.com/oauth2/authorize"
ACUITY_OAUTH_TOKEN_URL = "https://acuityscheduling.com/oauth2/token"
ACUITY_OAUTH_DISCONNECT_URL = "https://acuityscheduling.com/oauth2/disconnect"
ACUITY_API_BASE = "https://acuityscheduling.com/api/v1"
