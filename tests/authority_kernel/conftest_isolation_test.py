from pathlib import Path

from app import check_service_versions as configured_version_check
from app.configuration import versions
from app.configuration.versions import VERSIONS_FILE


def test_authority_bootstrap_restores_real_versions_module() -> None:
    assert versions.__file__ is not None
    assert Path(versions.__file__).name == "versions.py"
    assert VERSIONS_FILE.name == ".service-versions"
    assert configured_version_check is versions.check_service_versions


def test_authority_bootstrap_does_not_leak_synthetic_cookie_domain() -> None:
    from app.routes.middleware import COOKIE_DOMAIN, allowed_hosts

    assert COOKIE_DOMAIN != "testserver" or "testserver" in allowed_hosts()
