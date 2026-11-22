"""Readiness against a real PostgreSQL (the Compose `postgres-primary`, exposed on :5432).

Requires the stack to be running (`make up`). Override with TEST_DB_URL if needed.
"""

import os

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.markers import scenario

_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "app_local_pw")
DB_URL = os.environ.get("TEST_DB_URL", f"postgresql://app:{_PASSWORD}@localhost:5432/urlshortener")
UNREACHABLE_DB_URL = "postgresql://app:x@127.0.0.1:1/urlshortener?connect_timeout=1"


def ready(db_url: str):
    settings = Settings(app_env="test", db_primary_url=db_url, readiness_db_timeout_ms=1500)
    with TestClient(create_app(settings)) as client:  # runs lifespan: opens the pool
        return client.get("/health/ready")


@scenario("service-health", "Database reachable")
def test_ready_with_real_database():
    resp = ready(DB_URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "UP" and body["components"]["postgresPrimary"] == "UP"


@scenario("service-health", "Database unavailable")
def test_not_ready_when_database_unreachable():
    resp = ready(UNREACHABLE_DB_URL)
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "DOWN" and body["components"]["postgresPrimary"] == "DOWN"
