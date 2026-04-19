from decouple import config

ENCRYPTION_KEY = config("ENCRYPTION_KEY", cast=str)
