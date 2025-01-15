import logging
from pathlib import Path

import openai
from decouple import RepositoryEmpty, config
from openai import OpenAI

from app.setup import get_root_path
from app.utils.patching import hash_function_code

# OPENAI_API_KEY is automatically sourced from the ENV
openai = OpenAI()


def openai_logging_setup():
    """
    Config below is subject to change

    https://stackoverflow.com/questions/76256249/logging-in-the-open-ai-python-library/78214464#78214464
    https://github.com/openai/openai-python/blob/de7c0e2d9375d042a42e3db6c17e5af9a5701a99/src/openai/_utils/_logs.py#L16
    https://www.python-httpx.org/logging/

    Related gist: https://gist.github.com/iloveitaly/aa616d08d582c20e717ecd047b1c8534
    """

    # TODO this doesn't seem right for prod
    openai_log_path = get_root_path() / "tmp/openai.log"
    openai_file_handler = logging.FileHandler(openai_log_path)

    openai_logger = logging.getLogger("openai")
    openai_logger.propagate = False
    openai_logger.handlers = []  # Remove all handlers
    openai_logger.addHandler(openai_file_handler)

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.propagate = False
    httpx_logger.handlers = []  # Remove all handlers
    httpx_logger.addHandler(openai_file_handler)


def configure_openai():
    openai_logging_setup()
