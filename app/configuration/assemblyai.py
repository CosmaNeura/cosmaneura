import assemblyai
from decouple import config


def configure_assemblyai():
    pass


ASSEMBLY_API_KEY = config("ASSEMBLY_API_KEY", cast=str)
assemblyai.settings.api_key = ASSEMBLY_API_KEY
