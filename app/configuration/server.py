from decouple import config

# Public origin of this backend (e.g. https://app.cosmaneura.com). Used to build
# absolute callback URLs that external services hit back, such as the zak_url
# that Recall.ai requests when a bot joins a Zoom meeting.
API_BASE_URL: str = config("API_BASE_URL", cast=str).rstrip("/")
