"""Black-box tests through Nginx: routing, probes and API conventions (non-disruptive)."""

import time
from urllib.parse import urlparse

import pytest

from tests.api.stack import BASE_URL, logs_since, utc_now, wait_until
from tests.markers import scenario

PROBLEM = "application/problem+json"


@scenario("service-health", "Process is running")
def test_liveness(http):
    resp = http.get("/health/live")
    assert resp.status_code == 200 and resp.json() == {"status": "UP"}


@scenario("service-health", "Database reachable")
def test_readiness_through_edge(http):
    resp = http.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "UP"
    assert resp.json()["components"]["postgresPrimary"] == "UP"


@scenario("service-health", "Optional dependencies not configured")
def test_optional_components_disabled_through_edge(http):
    comps = http.get("/health/ready").json()["components"]
    assert all(comps[c] == "DISABLED" for c in ("postgresReplica", "redis", "kafka", "clickhouse"))


@scenario("service-health", "Host port already in use")
def test_platform_served_on_configured_port(http):
    port = urlparse(BASE_URL).port
    assert http.get("/health/live").status_code == 200
    # On this machine 8080 is taken by another project, so .env sets 8081.
    print(f"platform reachable on configured HTTP_PORT={port}")


@scenario("service-health", "Short-code-shaped path reaches the service")
def test_code_shaped_path_reaches_service(http):
    resp = http.get("/abcd123")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == PROBLEM
    assert resp.headers.get("x-request-id")


@scenario("service-health", "Other paths rejected at the edge")
@pytest.mark.parametrize("path", ["/a/b/c", "/ab", "/favicon.ico"])
def test_other_paths_rejected_by_nginx(http, path):
    since = utc_now()
    resp = http.get(path)
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]  # Nginx page, not problem+json
    assert "x-request-id" not in resp.headers
    lines = wait_until(
        lambda: [ln for ln in logs_since("nginx", since) if ln.get("uri") == path],
        timeout=10,
        message=f"nginx log line for {path}",
    )
    assert lines[-1]["up"] == ""  # no upstream contacted


@scenario("service-health", "Metrics hidden")
def test_metrics_hidden(http):
    assert http.get("/metrics").status_code == 404


@scenario("api-conventions", "Error body shape")
@scenario("api-conventions", "Unknown API path")
def test_unknown_api_path_through_edge(http):
    resp = http.get("/api/v1/does-not-exist")
    assert resp.status_code == 404 and resp.headers["content-type"] == PROBLEM
    body = resp.json()
    assert {"type", "title", "status", "code", "detail", "instance", "requestId"} <= body.keys()
    assert body["code"] == "NOT_FOUND" and body["requestId"] == resp.headers["x-request-id"]


@scenario("api-conventions", "Invalid JSON")
def test_invalid_json_through_edge(http, test_routes_enabled):
    resp = http.post(
        "/api/v1/__test/echo", content=b'{"broken":', headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400 and resp.json()["code"] == "VALIDATION_ERROR"


@scenario("api-conventions", "Oversized body")
def test_oversized_body_rejected(http):
    resp = http.post("/api/v1/anything", content=b"x" * 20 * 1024)
    assert resp.status_code == 413


@scenario("api-conventions", "Unhandled exception")
def test_unhandled_exception_through_edge(http, test_routes_enabled):
    since = utc_now()
    resp = http.get("/api/v1/__test/boom")
    assert resp.status_code == 500 and resp.json()["code"] == "INTERNAL_ERROR"
    assert "secret-internal-detail" not in resp.text and "Traceback" not in resp.text
    rid = resp.headers["x-request-id"]
    errors = wait_until(
        lambda: [
            ln
            for ln in logs_since("url-service", since)
            if ln.get("requestId") == rid and ln.get("level") == "error"
        ],
        timeout=10,
        message="error log line",
    )
    assert "secret-internal-detail" in errors[0]["exc"]


@scenario("api-conventions", "ID supplied by the edge")
def test_request_id_from_nginx_is_used_everywhere(http):
    since = utc_now()
    resp = http.get("/health/live")
    rid = resp.headers["x-request-id"]
    assert len(rid) == 32  # nginx $request_id format
    wait_until(
        lambda: any(ln.get("rid") == rid for ln in logs_since("nginx", since)),
        timeout=10,
        message="nginx access log with the same request id",
    )
    wait_until(
        lambda: any(ln.get("requestId") == rid for ln in logs_since("url-service", since)),
        timeout=10,
        message="service log with the same request id",
    )


@scenario("api-conventions", "Request log line")
def test_request_log_line_in_container_logs(http):
    since = utc_now()
    rid = http.get("/health/ready").headers["x-request-id"]
    line = wait_until(
        lambda: next(
            (
                ln
                for ln in logs_since("url-service", since)
                if ln.get("requestId") == rid and ln.get("msg") == "request completed"
            ),
            None,
        ),
        timeout=10,
        message="request log line",
    )
    assert line["method"] == "GET" and line["route"] == "/health/ready"
    assert line["status"] == 200 and isinstance(line["latencyMs"], int | float)


@scenario("api-conventions", "OpenAPI schema available")
def test_openapi_through_edge(http):
    resp = http.get("/api/openapi.json")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/json"
    assert {"/health/live", "/health/ready"} <= resp.json()["paths"].keys()


@scenario("api-conventions", "Interactive docs available")
def test_docs_through_edge(http):
    resp = http.get("/api/docs")
    assert resp.status_code == 200 and "/api/openapi.json" in resp.text


@scenario("api-conventions", "Default docs paths disabled")
def test_default_docs_disabled_through_edge(http):
    for path in ("/docs", "/redoc"):
        resp = http.get(path)
        assert resp.status_code == 404 and resp.headers["content-type"] == PROBLEM
    assert http.get("/openapi.json").status_code == 404  # rejected by Nginx (contains a dot)


def test_responses_are_fast(http):
    started = time.perf_counter()
    for _ in range(20):
        http.get("/health/live")
    assert (time.perf_counter() - started) / 20 < 0.2
