from decouple import config

RECALL_API_KEY = config("RECALL_API_KEY", cast=str)
RECALL_API_BASE = config(
    "RECALL_API_BASE", default="https://us-west-2.recall.ai/api/v1", cast=str
)
