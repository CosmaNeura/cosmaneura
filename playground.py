#!/usr/bin/env -S uv run ipython -i
# isort: off

from activemodel import SessionManager
from activemodel.utils import compile_sql

from app.configuration.clerk import clerk
from app.configuration.database import configure_database, database_url

# aliases for query building
from sqlmodel import SQLModel, select
import sqlalchemy as sa
import sqlmodel as sm

# autoimport packages
import app.models
import app.commands
import app.jobs

from sqlmodel import SQLModel
from playwright.async_api import async_playwright
import funcy_pipe as fp
from sqlmodel import create_engine
from activemodel.utils import find_all_sqlmodels
from app.configuration.clerk import clerk
import ipython_playground

# make all sqlmodels available in the repl
globals().update(find_all_sqlmodels(app.models))

doctor = Doctor.sample()

from activemodel.session_manager import _session_context, global_session, aglobal_session


def create_user():
	clerk.users.create({"email_address": "test@test.com"})


def delete_all_users():
	clerk.users.list() | fp.filter(lambda user: user.email_addresses[0].email_address != "mike+clerk_test@example.com") | fp.pluck_attr("id") | fp.lmap(lambda uid: clerk.users.delete(user_id=uid))


configure_database()

# treat the entire ipython session as a with block :)
engine = create_engine(database_url(), echo=True)
session = SessionManager.get_instance().get_session().__enter__()
_session_context.set(session)

def sa_run(stmt):
      result = session.execute(stmt).all()
      return result

def sa_sql(stmt):
      return compile_sql(stmt)

# https://github.com/mher/flower/issues/1348
def retry_task(task_id):
    from app.celery import celery
    meta = celery.backend.get_task_meta(task_id)
    task = celery.tasks[meta['name']]
    task.apply_async(args=meta['args'], kwargs=meta['kwargs'])

# https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints
def azure_openai_call():
     client = AzureOpenAI(api_key="key_from_1p", azure_endpoint="https://northcentralus.api.cognitive.microsoft.com/", api_version="2025-01-01-preview")
     client.chat.completions.create(model="gpt-4o", messages=[{"role":"system", "content":"tell me about yourself"}])

ipython_playground.output()
