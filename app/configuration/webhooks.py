from decouple import config

# Recall/Svix webhook signing secrets are endpoint-specific credentials for
# older Recall workspaces and workspace-wide credentials for newer ones.  The
# application deliberately treats the value as an opaque API-only secret.
RECALL_WEBHOOK_SECRET = config("RECALL_WEBHOOK_SECRET", default="", cast=str)

# Deployment starts quiesced.  Operators enable delivery only after the
# migration, worker, scheduler and health checks are present and legacy Redis
# work has drained (docs/RECALL_WEBHOOK_INBOX_DESIGN.md).
DURABLE_WEBHOOK_INBOX_ENABLED = config(
    "DURABLE_WEBHOOK_INBOX_ENABLED", default=False, cast=bool
)
