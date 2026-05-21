import httpx
from openai import AzureOpenAI

# OPENAI_API_KEY is automatically sourced from the ENV
# https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints
#
# Without an explicit timeout the SDK defaults to 600s. That's long enough
# for a single hung LLM call to occupy a celery prefork slot indefinitely
# — today (2026-05-19) five parallel summarize jobs hit a slow OpenAI
# request, exhausted all four prefork slots, and stopped the worker from
# processing any further webhooks. 120s read + 10s connect bounds the
# blast radius; long-running summarize work is bounded separately by
# celery's task_time_limit.
openai = AzureOpenAI(
    # api key and endpoint URL are sourced from the environment
    api_version="2025-03-01-preview",
    timeout=httpx.Timeout(120.0, connect=10.0),
)

DEFAULT_MULTI_MODAL_MODEL = "gpt-4.1-2025-04-14"


def configure_openai():
    pass
