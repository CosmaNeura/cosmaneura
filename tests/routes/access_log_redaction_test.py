from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter, FastAPI
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
    assert "query" not in serialized


def test_generic_dynamic_path_and_query_are_never_logged() -> None:
    access_logging = _load_access_logging()
    captured = _CapturingLog()
    setattr(access_logging, "log", captured)

    app = FastAPI()

    @app.get("/patients/{patient_id}")
    def patient(patient_id: str) -> dict[str, str]:
        return {"patient_id": patient_id}

    access_logging.add_middleware(app)
    patient_id = "patient-path-private-sentinel"
    query = "clinical-query-private-sentinel"
    with TestClient(app) as client:
        response = client.get(f"/patients/{patient_id}?search={query}")

    assert response.status_code == 200
    serialized = json.dumps(captured.records, sort_keys=True)
    assert patient_id not in serialized
    assert query not in serialized
    assert "/patients/{patient_id}" in serialized
    assert "query" not in serialized
    assert "client_ip" not in serialized


def test_unmatched_path_and_query_are_never_logged() -> None:
    access_logging = _load_access_logging()
    captured = _CapturingLog()
    setattr(access_logging, "log", captured)

    app = FastAPI()
    access_logging.add_middleware(app)
    path = "unmatched-path-private-sentinel"
    query = "unmatched-query-private-sentinel"
    with TestClient(app) as client:
        response = client.get(f"/{path}?search={query}")

    assert response.status_code == 404
    serialized = json.dumps(captured.records, sort_keys=True)
    assert path not in serialized
    assert query not in serialized
    assert "unmatched" in serialized


def test_nested_router_access_log_keeps_effective_template_and_leaf_name() -> None:
    access_logging = _load_access_logging()
    captured = _CapturingLog()
    setattr(access_logging, "log", captured)
    app = FastAPI()
    internal = APIRouter()
    patient_routes = APIRouter()

    @patient_routes.get("/{patient_id}/medications", name="patient_medications")
    def medications(patient_id: str) -> dict:
        return {"items": []}

    internal.include_router(patient_routes, prefix="/patients")
    app.include_router(internal, prefix="/internal/v1")
    access_logging.add_middleware(app)
    with TestClient(app) as client:
        response = client.get(
            "/internal/v1/patients/synthetic-private-id/medications?private=query"
        )
    assert response.status_code == 200
    assert (
        captured.records[0]["path"] == "/internal/v1/patients/{patient_id}/medications"
    )
    assert captured.records[0]["route"] == "patient_medications"
    assert "synthetic-private-id" not in json.dumps(captured.records)
    assert "private=query" not in json.dumps(captured.records)


def test_global_access_logger_is_app_owned_and_uvicorn_access_log_is_disabled() -> None:
    middleware = (ROOT / "app/routes/middleware/__init__.py").read_text(
        encoding="utf-8"
    )
    server = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "access_logging.add_middleware(app)" in middleware
    assert "fastapi_access_logger.add_middleware(app)" not in middleware
    assert "access_log=False" in server
