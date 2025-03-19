#!/usr/bin/env -S uv run ipython -i

# isort: off

from activemodel import SessionManager
import sqlalchemy as sa

from sqlmodel import SQLModel

from app.configuration.clerk import clerk
from app.configuration.database import configure_database, database_url
import app.models
import app.commands

from sqlmodel import SQLModel
from playwright.async_api import async_playwright
import funcy_pipe as fp
from sqlmodel import create_engine
import importlib
from activemodel.utils import find_all_sqlmodels
from app.configuration.clerk import clerk
import ipython_playground

# make all sqlmodels available in the repl
globals().update(find_all_sqlmodels(app.models))

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

# https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints
def azure_openai_call():
     client = AzureOpenAI(api_key="key_from_1p", azure_endpoint="https://northcentralus.api.cognitive.microsoft.com/", api_version="2025-01-01-preview")
     client.chat.completions.create(model="gpt-4o", messages=[{"role":"system", "content":"tell me about yourself"}])

ipython_playground.output()
