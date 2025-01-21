# Whether or not you use a Procfile in production, it's a great a way to canonically define your process types.
# Since this is effectively a simple yaml file, you can extract process types using yq or other tooling rather than
# scattering and duplicating process definitions throughout your codebase.

api: fastapi run --port ${PORT}
worker: celery -A app.celery worker
scheduler: celery -A app.celery beat
console: bash -l
