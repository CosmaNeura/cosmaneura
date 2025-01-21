#!/usr/bin/env -S uv run ipython -i

# isort: off

from activemodel import SessionManager
import sqlalchemy as sa

from sqlmodel import SQLModel
from app.configuration.clerk import clerk
from app.configuration.database import configure_database, database_url
import app.models

import inspect
from typing import get_type_hints
from rich.console import Console
from rich.text import Text
from pathlib import Path
import inspect
from sqlmodel import SQLModel
import logging
from playwright.async_api import async_playwright
import funcy_pipe as fp
from sqlmodel import create_engine
from redis import Redis
import pkgutil
import importlib


def inspect_environment():
    """Display relevant custom functions and variables with minimal formatting"""

    console = Console()
    width = console.width
    current_module = inspect.currentframe().f_globals
    ipython_path = Path.home() / ".ipython"
    builtin_modules = {'os', 'sys', 'json', 'tempfile', 'subprocess', 'importlib', 'pkgutil'}
    ipy_modules = {'IPython', 'ipykernel'}
    exclude_vars = {'In', 'Out', 'PIPE', 'get_ipython', 'exit', 'quit', 'c'}

    def truncate_text(text: str, max_width: int) -> str:
        if len(text) > max_width:
            return text[:max_width-3] + "..."
        return text

    def get_module_info(module) -> str:
        module_path = getattr(module, '__path__', [''])[0]
        version = getattr(module, '__version__', 'unknown version')
        return f"{module.__name__} ({version}) from {module_path}"

    # Functions Section
    console.print("\n[bold blue]Custom Functions[/bold blue]")
    console.print("─" * width)

    for name, obj in current_module.items():
        if (inspect.isfunction(obj) and
            not name.startswith('_') and
            obj.__module__ == '__main__'):
            # Get the source file of the function
            try:
                source_file = Path(inspect.getfile(obj))
                # Skip if function is from .ipython directory
                if ipython_path in source_file.parents:
                    continue
            except (TypeError, ValueError):
                continue

            sig = str(inspect.signature(obj))
            return_type = get_type_hints(obj).get('return', None)
            if return_type:
                sig += f" -> {return_type.__name__}"
            text = Text()
            text.append(f"{name:<30}", style="cyan bold")
            text.append(truncate_text(sig, width-30), style="green")
            console.print(text)

    # Modules Section
    console.print("\n[bold blue]Imported Modules[/bold blue]")
    console.print("─" * width)

    for name, obj in current_module.items():
        if (inspect.ismodule(obj) and
            not name.startswith('_') and
            name not in builtin_modules and
            name not in exclude_vars):
            text = Text()
            text.append(f"{name:<30}", style="cyan bold")
            text.append(truncate_text(get_module_info(obj), width-30), style="yellow")
            console.print(text)

    # Variables Section
    console.print("\n[bold blue]Variables[/bold blue]")
    console.print("─" * width)

    for name, obj in current_module.items():
        if (not inspect.isfunction(obj) and
            not inspect.ismodule(obj) and
            not inspect.isclass(obj) and
            not name.startswith('_') and
            name not in builtin_modules and
            name not in exclude_vars):
            type_info = type(obj).__name__
            if hasattr(obj, '__annotations__'):
                annotations = getattr(obj, '__annotations__', {})
                if annotations:
                    type_info += f" [{', '.join(str(v) for v in annotations.values())}]"
            text = Text()
            text.append(f"{name:<30}", style="cyan bold")
            text.append(truncate_text(type_info, width-30), style="green")
            console.print(text)


def import_all_models(module):
    """Import all model classes from module and submodules into current namespace."""

    log = logging.getLogger(__name__)
    log.debug(f"Starting model import from module: {module.__name__}")
    model_classes = {}

    # Walk through all submodules
    for loader, module_name, is_pkg in pkgutil.walk_packages(module.__path__):
        full_name = f"{module.__name__}.{module_name}"
        log.debug(f"Importing submodule: {full_name}")
        submodule = importlib.import_module(full_name)

        # Get all classes from module
        for name, obj in inspect.getmembers(submodule):
            if (inspect.isclass(obj) and
                issubclass(obj, SQLModel) and
                obj != SQLModel):
                log.debug(f"Found model class: {name}")
                model_classes[name] = obj

                # Export to current namespace
                globals()[name] = obj
                log.debug(f"Exported {name} to global namespace")

    log.debug(f"Completed model import. Found {len(model_classes)} models")
    return model_classes

import_all_models(app.models)

# TODO not sure if this works
def patch_reload():
	# Store the original reload function
	_original_reload = importlib.reload

	def custom_reload(module):
		"""
		Custom reload function that wraps importlib.reload and resets
		SQLModel metadata after reloading the module.
		"""
		log.info("Clearing metadata before reload...")
		SQLModel.metadata.clear()

		result = _original_reload(module)
		return result

	# Replace the original reload with the custom one
	importlib.reload = custom_reload

patch_reload()

# TODO this is so bad and dangerous
def iterate_on_model(model: SQLModel):
    from app.configuration.database import get_engine

    SQLModel.metadata.drop_all(
        tables=[SQLModel.metadata.tables[model.__tablename__]], bind=get_engine()
    )

    SQLModel.metadata.create_all(get_engine())

from activemodel.session_manager import global_session, aglobal_session

def global_playground_session():
    session_context = global_session()
    session_context.__enter__()

    return session_context

def global_playground_session_2():
    "setup a global session to lazy field don't fail on us"
    from contextlib import nullcontext
    return nullcontext(global_session())

async def aglobal_playground_session():
    """Setup a global async session that persists for interactive development"""
    from contextlib import nullcontext
    session_context = aglobal_session()
    # Get the first yield value from the async generator
    context = await anext(session_context.__aiter__())
    return nullcontext(context)

# aglobal_playground_session()
ctx = global_playground_session()

def global_session_connection():
    """Setup a global connection with transaction control for interactive development"""
    engine = SessionManager.get_instance().get_engine()

    ctx_connection = engine.begin()

    # looks like if this is garbage collected, it will close the connection
    connection = ctx_connection.__enter__()

    # Store connection in session manager
    SessionManager.get_instance().session_connection = connection
    return ctx_connection

# ctx = global_session_connection()

async def playwright_page():
	"""
	>>> page = await playwright_page()
	>>> response = await page.goto("https://www.google.com")
	>>> response.status
	"""


	playwright = await async_playwright().start()
	browser = await playwright.chromium.launch(headless = True)
	page = await browser.new_page()
	return page

def create_user():
	clerk.users.create({"email_address": "test@test.com"})


def delete_all_users():
	clerk.users.list() | fp.filter(lambda user: user.email_addresses[0].email_address != "mike+clerk_test@example.com") | fp.pluck_attr("id") | fp.lmap(lambda uid: clerk.users.delete(user_id=uid))


configure_database()
engine = create_engine(database_url(), echo=True)
session = SessionManager.get_instance().get_session().__enter__()

def get_db_version() -> tuple[str, str]:
	with engine.connect() as conn:
		pg_version = conn.execute(sa.text("SHOW server_version")).scalar()
		# Extract major.minor version from full version string
		pg_version = pg_version.split()[0]

	return pg_version

def get_redis_version(redis_url: str):
	# Get Redis version
	redis_client = Redis.from_url(redis_url)
	redis_version = redis_client.info()['redis_version']

	return redis_version

# https://github.com/mher/flower/issues/1348
def retry_task(task_id):
    from app.celery import celery
    meta = celery.backend.get_task_meta(task_id)
    task = celery.tasks[meta['name']]
    task.apply_async(args=meta['args'], kwargs=meta['kwargs'])

# with Session(get_engine()) as session:
# 	user = Doctor(clerk_id="hi")
# 	llm_response = LLMResponse(
# 		model="gpt-3.5-turbo",
# 		category="test",
# 		prompt="hello",
# 		response="world",
# 		prompt_hash="hi"
# 	)
# 	session.add(llm_response)
# 	session.add(user)
# 	session.commit()
# 	# session.close()

# llm_response = LLMResponse(
# 	model="gpt-3.5-turbo",
# 	category="test",
# 	prompt="hello from save",
# 	response="world",
# )
# llm_response.save()

# from activemodel import BaseModel as ActiveModelBaseModel
# from pydantic import validate_model

# # TODO this approach isn't great, it model_validate copies the entire model when successful
# class TestSQLModelValidationWithConstructor(ActiveModelBaseModel):
#     def __init__(self, **data) -> None:
#         super().__init__(**data)
#         _, _, validation_error = self.__class__.model_validate(self.__class__, data)
#         if validation_error:
#             raise validation_error

#     @model_validator(mode="before")
#     @classmethod
#     def before_validation(cls, data):
#          breakpoint()

# class TestValidationModel(BaseModel):
#     prompt: str
#     prompt_hash: str | None

#     @model_validator(mode="before")
#     @classmethod
#     def check_card_number_omitted(cls, data):
#         if isinstance(data, dict):
#             assert "card_number" not in data, "card_number should not be included"
#         return data

#     @field_validator("prompt_hash")
#     @classmethod
#     def prevent_explicit_hash(cls, v):
#         if v is not None:
#             raise ValueError("prompt_hash cannot be set explicitly")
#         return v

# # TestValidationModel(prompt="foo", prompt_hash="bar")

# Call it whenever needed
inspect_environment()
