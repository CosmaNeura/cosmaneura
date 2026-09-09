from decouple import config

GOOGLE_CALENDAR_CLIENT_ID = config("GOOGLE_CALENDAR_CLIENT_ID", default="", cast=str)
GOOGLE_CALENDAR_CLIENT_SECRET = config(
    "GOOGLE_CALENDAR_CLIENT_SECRET", default="", cast=str
)
GOOGLE_CALENDAR_REDIRECT_URI = config(
    "GOOGLE_CALENDAR_REDIRECT_URI", default="", cast=str
)
GOOGLE_CALENDAR_SUCCESS_REDIRECT = config(
    "GOOGLE_CALENDAR_SUCCESS_REDIRECT", default="", cast=str
)

GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
# read-only: this platform never writes back to a doctor's calendar
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
