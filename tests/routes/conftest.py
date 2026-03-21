# pyright: reportMissingImports=false

import typing as t

import pytest
from clerk_backend_api import User as ClerkUser
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from tests.routes.utils import (
    MockAuthenticateRequest,
    base_server_url,
)


# TODO should move to activemodel
# TODO I wonder how the other factory library handles this?
@pytest.fixture
def global_session():
    """
    By default, factories do not use the same session as the `save()` method.

    This fixture ensures that a global factory is set and used for all factories *and* other ORM operations.
    """

    # TODO may be better to set some sort of session state on the ORM level so the factory boy session could be passed
    # down. This doesn't seem like the best flow

    from activemodel.session_manager import (
        global_session as activemodel_global_session,
    )

    with activemodel_global_session() as session:
        assert session

        from factory.alchemy import SQLAlchemyModelFactory

        # Ensure that all factories use the same session
        for factory in SQLAlchemyModelFactory.__subclasses__():
            factory._meta.sqlalchemy_session = session

        yield session


@pytest.fixture
def api_client():
    from app.server import api_app

    with TestClient(api_app, base_url="api.example.com") as client:
        yield client


@pytest.fixture
def client():
    "client to connect to your fastapi routes"
    from app.server import api_app

    with TestClient(api_app, base_url=base_server_url()) as client:
        client.cookies.clear()
        yield client


@pytest.fixture
def authenticated_client():
    "mocks out the clerk authentication and returns a static response"

    from app.routes.internal import authenticate_clerk_request_middleware
    from app.server import api_app

    api_app.dependency_overrides[authenticate_clerk_request_middleware] = (
        MockAuthenticateRequest()
    )

    with TestClient(api_app, base_url=base_server_url()) as client:
        client.cookies.clear()

        def override_user(user: ClerkUser | None):
            api_app.dependency_overrides[authenticate_clerk_request_middleware] = (
                MockAuthenticateRequest(user=user)
            )

        client.override_user = override_user  # type: ignore[attr-defined]

        yield client

    api_app.dependency_overrides = {}


@pytest.fixture
async def aclient() -> t.AsyncGenerator[AsyncClient, None]:
    from app.server import api_app

    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url=base_server_url(),
    ) as client:
        client.cookies.clear()
        yield client
