from decouple import config

NO_COLOR = config("NO_COLOR", cast=bool, default=False)
"supports NO_COLOR standard: https://no-color.org/"

INTAKE_CHAT_DOMAIN = config("INTAKE_CHAT_DOMAIN", cast=str)
"domain for the intake chat URL, this is separate from the main app url to not confuse users"

MAX_INTAKE_QUESTIONS = 5
"hard cap on how many questions an intake chat asks a patient, whether provider-defined or AI-generated"
