from openai import AzureOpenAI

# OPENAI_API_KEY is automatically sourced from the ENV
# https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints
openai = AzureOpenAI(
    # api key and endpoint URL are sourced from the environment
    api_version="2025-03-01-preview",
)

DEFAULT_MULTI_MODAL_MODEL = "gpt-4.1-2025-04-14"


def configure_openai():
    pass
