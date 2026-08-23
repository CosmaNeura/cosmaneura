# Whether or not you use a Procfile in production, it's a great a way to canonically define your process types.
# Since this is effectively a simple yaml file, you can extract process types using yq or other tooling rather than
# scattering and duplicating process definitions throughout your codebase.

api: python main.py
# worker runs WITHOUT --beat. The scheduler runs in its own container so a
# stuck beat (redbeat lock contention, scheduler bug) can't take down task
# execution, and a worker crash doesn't pause the schedule.
worker: celery -A app.celery.celery_app worker
scheduler: celery -A app.celery.celery_app beat --scheduler redbeat.RedBeatScheduler
job_monitor: celery -A app.celery.celery_app flower --basic-auth=:$FLOWER_PASSWORD --port=$FLOWER_PORT --enable_events=False --persistent=False --tasks_columns=name,uuid,state,received,started,runtime,worker
console: bash -l
