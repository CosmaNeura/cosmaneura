from decouple import config
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from app.environments import is_development
from app.routes.dependencies.timing import add_timing_middleware

from activemodel.session_manager import SessionManager


def add_database_session_middleware(app: FastAPI):
    """
    Add a middleware that creates a database session for each request.
    """

    @app.middleware("http")
    async def db_session_middleware(request: Request, call_next):
        with SessionManager.get_instance().global_session():
            return await call_next(request)


def add_middleware(app: FastAPI):
    """
    Entrypoint to add all middleware for the root router:

    - Not requiring HTTPS here since it is assumed we'll be behind a proxy server
    """
    # even in development, some sort of CORS is required
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # add_database_session_middleware(app)
    add_timing_middleware(app)

    if is_development() and config("FASTAPI_DEBUG", cast=bool, default=False):
        from app.utils.debug import PdbMiddleware

        # app.add_middleware(PdbrMiddleware, debug=True)
        app.add_middleware(PdbMiddleware, debug=True)

    return app
