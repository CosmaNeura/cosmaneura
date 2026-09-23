"""
The react-router application is setup as a SPA. This logic configures the fast API server to serve the static SPA assets.

There are some nuances we need to consider:

* The generated SPA API client use a specific host for access. This changes depending
  on the environment. This means, in order to run integration tests on the application
  we need to build the assets in an environment distinct from production, with the right
  target host configured.

* In development, we don't want to statically serve the assets. We want to use live reloaded react for a fast dev loop.
  The logic here is only used in production and integration tests, which reduces the test coverage and makes this logic
  more risky.

* At-scale, it makes sense to host assets on a CDN. However, serving assets out of a single container simplifies the
  deployment process and should be performant enough for most applications.


"""

import mimetypes

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import Scope

from app import log, root
from app.environments import (
    is_development,
    is_local_testing,
    is_production,
    is_staging,
    python_environment,
)


class GZipStaticFiles(StaticFiles):
    """Serve precompressed assets through Starlette's protected file response."""

    async def get_response(self, path: str, scope: Scope):
        if "\x00" in path:
            raise HTTPException(status_code=404)
        encodings = Headers(scope=scope).get("Accept-Encoding", "")
        accepts_gzip = False
        for encoding in encodings.split(","):
            token, *parameters = encoding.strip().lower().split(";")
            if token != "gzip":
                continue
            try:
                quality = next(
                    (
                        float(part.strip()[2:])
                        for part in parameters
                        if part.strip().startswith("q=")
                    ),
                    1.0,
                )
                accepts_gzip = 0 < quality <= 1
            except ValueError:
                accepts_gzip = False
        if accepts_gzip:
            try:
                response = await super().get_response(path + ".gz", scope)
            except HTTPException as error:
                if error.status_code != 404:
                    raise
            else:
                response.headers["Content-Encoding"] = "gzip"
                content_type = (
                    mimetypes.guess_type(path)[0] or "application/octet-stream"
                )
                if content_type.startswith("text/"):
                    content_type += f"; charset={response.charset}"
                response.headers["Content-Type"] = content_type
                response.headers.append("Vary", "Accept-Encoding")
                return response
        response = await super().get_response(path, scope)
        response.headers.append("Vary", "Accept-Encoding")
        return response


def mount_public_directory(app: FastAPI):
    # TODO should be extracted into another env var so it can be shared with JS
    if is_production() or is_staging():
        public_path = root / "public"
    else:
        public_path = root / "web/build" / python_environment() / "client"

    # in development, a separate py & js server will be used, if the development build DNE that's fine
    if not public_path.exists() and (is_development() or is_local_testing()):
        log.warning(
            "The development build does not exist. Static files will not be served"
        )
        return app

    if not public_path.exists():
        raise Exception(
            f"Client assets do not exist {public_path}. Please run `just py_js-build`"
        )

    app.mount(
        "/assets",
        GZipStaticFiles(directory=public_path / "assets", html=False),
        name="public",
    )

    @app.get("/", include_in_schema=False)
    async def javascript_index():
        return FileResponse(public_path / "index.html")

    public_files = StaticFiles(directory=public_path, html=False)

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend_handler(path: str, request: Request):
        """
        This is a very dangerous piece of code: if this is not last it will override other routes in the application

        Without this, non-index RR routes will not work. When a path is requested that does not exist in the fastapi
        application, it servers up the index.html file. Most likely, this path is a RR route.

        https://gist.github.com/ultrafunkamsterdam/b1655b3f04893447c3802453e05ecb5e
        """
        if "\x00" in path:
            raise HTTPException(status_code=404)
        try:
            return await public_files.get_response(path, request.scope)
        except HTTPException as error:
            if error.status_code != 404:
                raise
        return FileResponse(public_path / "index.html")

    return app
