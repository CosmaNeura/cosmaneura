from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _load_access_logging() -> ModuleType:
    path = ROOT / "app/routes/middleware/access_logging.py"
    spec = importlib.util.spec_from_file_location("access_logging_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _CapturingLog:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def info(self, event: str, **values) -> None:
        self.records.append({"event": event, **values})

    def debug(self, event: str, **values) -> None:
        self.records.append({"event": event, **values})


def test_recall_zak_access_log_redacts_path_token_and_query() -> None:
    access_logging = _load_access_logging()
    captured = _CapturingLog()
    setattr(access_logging, "log", captured)

    app = FastAPI()

    @app.get("/external/v1/recall/zak/{token}")
    def recall_zak(token: str) -> dict[str, str]:
        return {"token": token}

    access_logging.add_middleware(app)
    raw_token = "private-zak-bearer-sentinel"
    raw_query = "patient-private-query-sentinel"
    with TestClient(app) as client:
        response = client.get(
            f"/external/v1/recall/zak/{raw_token}?context={raw_query}"
        )

    assert response.status_code == 200
    serialized = json.dumps(captured.records, sort_keys=True)
    assert raw_token not in serialized
    assert raw_query not in serialized
    assert "/external/v1/recall/zak/{token}" in serialized
    assert "[redacted]" in serialized


def test_noncredential_access_target_is_unchanged() -> None:
    access_logging = _load_access_logging()
    target = access_logging.safe_access_target(
        {"path": "/healthcheck", "query_string": b"probe=ready"}
    )

    assert target.path == "/healthcheck"
    assert target.query == "probe=ready"
    assert target.target == "/healthcheck?probe=ready"


def test_global_access_logger_is_app_owned_and_uvicorn_access_log_is_disabled() -> None:
    middleware = (ROOT / "app/routes/middleware/__init__.py").read_text(
        encoding="utf-8"
    )
    server = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "access_logging.add_middleware(app)" in middleware
    assert "fastapi_access_logger.add_middleware(app)" not in middleware
    assert "access_log=False" in server
