# pyright: reportMissingImports=false

import typing as t

import pytest
from clerk_backend_api import User as ClerkUser
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from tests.routes.utils import (
    MockAuthenticateRequest,
    base_server_url,
    get_clerk_dev_user,
    provision_practice_in_force,
)

VIEW_AS_BINDING_HEADER = "X-Cosmaneura-View-As-Binding"
INACTIVE_VIEW_AS_BINDING = "inactive"


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

        try:
            from factory.alchemy import SQLAlchemyModelFactory

            # Ensure that all factories use the same session
            for factory in SQLAlchemyModelFactory.__subclasses__():
                factory._meta.sqlalchemy_session = session
        except ModuleNotFoundError:
            pass

        yield session


@pytest.fixture
def api_client():
    from app.server import api_app

    with TestClient(api_app, base_url="api.example.com") as client:
        client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING
        yield client


@pytest.fixture
def client():
    "client to connect to your fastapi routes"
    from app.server import api_app

    with TestClient(api_app, base_url=base_server_url()) as client:
        client.cookies.clear()
        client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING
        yield client


def _mocked_clerk_client(provision: bool):
    "mocks out the clerk authentication and returns a static response"

    from app.routes.internal import authenticate_clerk_request_middleware
    from app.server import api_app

    from app.models.doctor import Doctor

    _, _, dev_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=dev_user.id)
    if not doctor.email:
        assert dev_user.email_addresses
        doctor.email = dev_user.email_addresses[0].email_address
        doctor.save()

    if provision:
        # the practice gate fails closed: most route tests need the dev doctor
        # inside a practice whose BAA is in force and which is entitled
        provision_practice_in_force(doctor)

    api_app.dependency_overrides[authenticate_clerk_request_middleware] = (
        MockAuthenticateRequest()
    )

    with TestClient(api_app, base_url=base_server_url()) as client:
        client.cookies.clear()
        client.headers[VIEW_AS_BINDING_HEADER] = INACTIVE_VIEW_AS_BINDING

        def override_user(user: ClerkUser | None):
            api_app.dependency_overrides[authenticate_clerk_request_middleware] = (
                MockAuthenticateRequest(user=user)
            )

        client.override_user = override_user  # type: ignore[attr-defined]

        yield client

    api_app.dependency_overrides = {}


@pytest.fixture
def authenticated_client():
    "mocked clerk auth with the dev doctor in a practice that passes the practice gate"
    yield from _mocked_clerk_client(provision=True)


@pytest.fixture
def bare_authenticated_client():
    """mocked clerk auth with a bare dev doctor (no practice) — for tests that
    exercise the onboarding / practice / BAA flow itself"""
    yield from _mocked_clerk_client(provision=False)


@pytest.fixture
async def aclient() -> t.AsyncGenerator[AsyncClient, None]:
    from app.server import api_app

    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url=base_server_url(),
        headers={VIEW_AS_BINDING_HEADER: INACTIVE_VIEW_AS_BINDING},
    ) as client:
        client.cookies.clear()
        yield client
