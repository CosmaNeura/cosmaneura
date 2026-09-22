import pytest
from fastapi import FastAPI
from starlette.routing import Route

import app.server as server
from app.configuration import public_onboarding as public_onboarding_configuration
from app.routes.public_onboarding import public_onboarding_api_app


def _app_with_latch(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> FastAPI:
    """Apply the real registration to a throwaway app.

    Deliberately not reloading `app.server`: that mutates module state the rest
    of the suite shares.
    """

    monkeypatch.setattr(server, "public_onboarding_configured", lambda: enabled)
    app = FastAPI()
    server.register_public_onboarding_routes(app)
    return app


def _paths(app: FastAPI) -> set[str]:
    return {route.path for route in app.routes if isinstance(route, Route)}


def test_latch_defaults_closed() -> None:
    """Nothing about the public lane is served unless deliberately enabled."""

    assert public_onboarding_configuration.PUBLIC_ONBOARDING_ENABLED is False
    assert public_onboarding_configuration.public_onboarding_configured() is False


def test_registration_reports_whether_it_included_the_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "public_onboarding_configured", lambda: False)
    assert server.register_public_onboarding_routes(FastAPI()) is False

    monkeypatch.setattr(server, "public_onboarding_configured", lambda: True)
    assert server.register_public_onboarding_routes(FastAPI()) is True


def test_route_is_absent_from_the_application_when_the_latch_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app_with_latch(monkeypatch, enabled=False)

    assert not any("public-onboarding" in path for path in _paths(app))


def test_route_is_absent_from_the_openapi_schema_when_the_latch_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent, not merely refusing — a dark surface should not be discoverable."""

    app = _app_with_latch(monkeypatch, enabled=False)
    schema = app.openapi()

    assert not any("public-onboarding" in path for path in schema.get("paths", {}))


def test_route_is_registered_when_the_latch_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app_with_latch(monkeypatch, enabled=True)

    assert "/public-onboarding/v1/capabilities" in _paths(app)


def _dependency_names() -> set[str]:
    # The Clerk guard is a callable instance rather than a function, so it is
    # identified by its type.
    return {
        getattr(d.dependency, "__name__", type(d.dependency).__name__)
        for d in public_onboarding_api_app.dependencies
    }


def test_capabilities_route_requires_authentication() -> None:
    """The latch controls exposure, never authorization."""

    names = _dependency_names()

    assert "AuthenticateClerkRequest" in names
    assert "inject_doctor_record" in names


def test_capabilities_route_does_not_gate_on_practice_in_force() -> None:
    """It must stay reachable to report *why* a practice cannot act.

    Applying `require_practice_in_force` here would refuse before the caller
    could learn which gate failed, which is the one thing this endpoint is for.
    """

    assert "require_practice_in_force" not in _dependency_names()
