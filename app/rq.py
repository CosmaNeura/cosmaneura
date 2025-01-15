from rq import Queue

from app.configuration.redis import get_redis

q = Queue(connection=get_redis())

# TODO do we need to do anything fancy for database connections?
