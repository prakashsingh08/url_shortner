"""In-process tests of api-conventions using FastAPI's TestClient (no database needed)."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.logging import JsonFormatter
from app.main import create_app
from tests.markers import scenario

PROBLEM_FIELDS = {"type", "title", "status", "code", "detail", "instance", "requestId"}


@pytest.fixture
def client():
    # Lifespan is not entered, so no database connection is attempted.
    app = create_app(Settings(app_env="test", db_primary_url="postgresql://x@127.0.0.1:1/x"))
    return TestClient(app, raise_server_exceptions=False)


def assert_problem(resp, status: int, code: str) -> dict:
    assert resp.status_code == status
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert PROBLEM_FIELDS <= body.keys()
    assert body["status"] == status and body["code"] == code
    assert body["requestId"] == resp.headers["x-request-id"]
    return body


@scenario("api-conventions", "Error body shape")
@pytest.mark.parametrize(
    "method,path,kwargs,status,code",
    [
        ("get", "/api/v1/nope", {}, 404, "NOT_FOUND"),
        ("delete", "/health/live", {}, 405, "METHOD_NOT_ALLOWED"),
        ("post", "/api/v1/__test/echo", {"json": {}}, 400, "VALIDATION_ERROR"),
        ("get", "/api/v1/__test/boom", {}, 500, "INTERNAL_ERROR"),
    ],
)
def test_every_error_is_problem_json(client, method, path, kwargs, status, code):
    body = assert_problem(getattr(client, method)(path, **kwargs), status, code)
    assert body["instance"] == path


@scenario("api-conventions", "Unknown API path")
def test_unknown_api_path(client):
    assert_problem(client.get("/api/v1/does-not-exist"), 404, "NOT_FOUND")


@scenario("api-conventions", "Invalid JSON")
def test_invalid_json_is_400(client):
    resp = client.post(
        "/api/v1/__test/echo", content=b'{"broken":', headers={"content-type": "application/json"}
    )
    body = assert_problem(resp, 400, "VALIDATION_ERROR")
    assert body["errors"] == [{"field": "body", "message": "invalid JSON"}]


def test_missing_field_lists_field_error(client):
    body = assert_problem(client.post("/api/v1/__test/echo", json={}), 400, "VALIDATION_ERROR")
    assert body["errors"][0]["field"] == "message"


@scenario("api-conventions", "Oversized body")
def test_body_over_16kb_is_413(client):
    resp = client.post(
        "/api/v1/__test/echo", content=b"x" * 20_000, headers={"content-type": "application/json"}
    )
    assert_problem(resp, 413, "PAYLOAD_TOO_LARGE")


@scenario("api-conventions", "Unhandled exception")
def test_unhandled_exception_hides_internals_but_logs_them(client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.error"):
        resp = client.get("/api/v1/__test/boom")
    body = assert_problem(resp, 500, "INTERNAL_ERROR")
    text = resp.text
    assert "secret-internal-detail" not in text and "Traceback" not in text
    assert "RuntimeError" not in text

    [record] = [r for r in caplog.records if r.name == "app.error"]
    assert record.requestId == body["requestId"]
    line = json.loads(JsonFormatter("url-service").format(record))
    assert line["level"] == "error" and line["requestId"] == body["requestId"]
    assert "secret-internal-detail" in line["exc"]


@scenario("api-conventions", "OpenAPI schema available")
def test_openapi_schema(client):
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/json"
    doc = resp.json()
    assert doc["openapi"].startswith("3.")
    assert {"/health/live", "/health/ready"} <= doc["paths"].keys()
    assert not any("__test" in p for p in doc["paths"])  # test routes stay out of the docs


@scenario("api-conventions", "Interactive docs available")
def test_interactive_docs(client):
    resp = client.get("/api/docs")
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    assert "/api/openapi.json" in resp.text


@scenario("api-conventions", "Default docs paths disabled")
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_default_docs_paths_disabled(client, path):
    assert_problem(client.get(path), 404, "NOT_FOUND")


def test_test_routes_absent_outside_test_env():
    app = create_app(Settings(app_env="local", db_primary_url="postgresql://x@127.0.0.1:1/x"))
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/v1/__test/boom").status_code == 404


@scenario("api-conventions", "Request log line")
def test_request_log_line_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        resp = client.get("/health/live")
    [record] = [r for r in caplog.records if r.name == "app.access"]
    line = json.loads(JsonFormatter("url-service").format(record))
    assert {"ts", "level", "service", "instance", "requestId", "msg"} <= line.keys()
    assert line["method"] == "GET" and line["route"] == "/health/live"
    assert line["status"] == 200 and isinstance(line["latencyMs"], int | float)
    assert line["requestId"] == resp.headers["x-request-id"]
    assert line["ts"].endswith("Z")
