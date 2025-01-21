# NOTE nixpacks triggers a python build when a main.py is present

# add directory of the current file to the python path
import os
import sys

sys.path.append(os.path.dirname(__file__))

from app.environments import is_development, is_testing

if is_testing() or is_development():
    raise RuntimeError("main.py should only be run directly in production")

# https://github.com/fastapi/fastapi-cli/blob/9a4741816dc288bbd931e693166117d98ee14dea/src/fastapi_cli/cli.py#L162-L171
import uvicorn
from decouple import config

from app.server import api_app

PORT = config("PORT", cast=int)

uvicorn.run(
    api_app,
    # bind on all interfaces in production
    host="0.0.0.0",
    port=PORT,
    # TODO we should tune this and have it pulled from env
    # workers=2,
    # NOTE important to ensure structlog controls logging in production
    log_config=None,
)
