"""Static serving must preserve directory boundaries after dependency upgrades."""

import gzip

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routes.static as static


@pytest.fixture
def site(tmp_path, monkeypatch):
    public = tmp_path / "public"
    assets = public / "assets"
    assets.mkdir(parents=True)
    (public / "index.html").write_text("synthetic SPA")
    (public / "favicon.ico").write_bytes(b"synthetic icon")
    (assets / "app.js").write_bytes(b"synthetic script")
    (assets / "app.js.gz").write_bytes(gzip.compress(b"synthetic script"))
    secret = tmp_path / "outside.txt"
    secret.write_text("outside-synthetic-sentinel")
    (assets / "escape.js.gz").symlink_to(secret)
    (public / "escape.txt").symlink_to(secret)
    monkeypatch.setattr(static, "root", tmp_path)
    monkeypatch.setattr(static, "is_production", lambda: True)
    app = static.mount_public_directory(FastAPI())
    return TestClient(app)


@pytest.mark.parametrize("encoding", ["gzip", "br, gzip;q=0.8"])
def test_precompressed_assets_keep_type_and_cache_variance(site, encoding):
    response = site.get("/assets/app.js", headers={"Accept-Encoding": encoding})
    assert response.status_code == 200
    assert response.content == b"synthetic script"
    assert "javascript" in response.headers["Content-Type"]
    assert response.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in response.headers["Vary"]


@pytest.mark.parametrize("encoding", ["identity", "gzip;q=0", "gzip;q=broken"])
def test_explicitly_unacceptable_gzip_serves_plain_file(site, encoding):
    response = site.get("/assets/app.js", headers={"Accept-Encoding": encoding})
    assert response.status_code == 200
    assert response.content == b"synthetic script"
    assert "Content-Encoding" not in response.headers


@pytest.mark.parametrize(
    "path", ["/assets/escape.js", "/escape.txt", "/%2e%2e/outside.txt"]
)
def test_static_routes_never_follow_paths_outside_public_directory(site, path):
    response = site.get(path, headers={"Accept-Encoding": "gzip"})
    assert b"outside-synthetic-sentinel" not in response.content
    assert response.status_code in {200, 404}


def test_static_methods_and_spa_fallback_survive_upgrade(site):
    assert (
        site.post("/assets/app.js", headers={"Accept-Encoding": "gzip"}).status_code
        == 405
    )
    assert site.get("/appointments/synthetic-route").text == "synthetic SPA"
    assert site.get("/favicon.ico").content == b"synthetic icon"
    response = site.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
    assert (
        site.get(
            "/assets/app.js",
            headers={
                "Accept-Encoding": "gzip",
                "If-None-Match": response.headers["ETag"],
            },
        ).status_code
        == 304
    )
