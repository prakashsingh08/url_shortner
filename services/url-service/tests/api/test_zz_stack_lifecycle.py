"""Disruptive tests: they stop/kill containers and restore the stack afterwards.

Named test_zz_* so they run after the non-disruptive API tests.
"""

import subprocess
import time
from datetime import datetime

import httpx
import pytest

from tests.api.stack import (
    BASE_URL,
    REPO,
    compose,
    container_ids,
    container_ip,
    docker,
    inspect,
    logs_since,
    ready_status,
    restore_stack,
    utc_now,
    wait_until,
)
from tests.markers import scenario


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def restore():
    yield
    restore_stack()


@scenario("service-health", "Clean start")
def test_stack_healthy_and_migrations_ran_first():
    for service in ("nginx", "postgres-primary", "url-service"):
        ids = container_ids(service)
        assert ids, f"{service} has no containers"
        for cid in ids:
            state = inspect(cid)["State"]
            assert state["Health"]["Status"] == "healthy", f"{service} {cid[:12]} not healthy"

    [migrate] = container_ids("migrate")
    m = inspect(migrate)["State"]
    assert m["Status"] == "exited" and m["ExitCode"] == 0
    for cid in container_ids("url-service"):
        assert ts(inspect(cid)["State"]["StartedAt"]) > ts(m["FinishedAt"])
    assert ready_status() == 200


@scenario("service-health", "Missing required configuration")
def test_service_exits_when_db_url_missing():
    result = compose(
        "run", "--rm", "--no-deps", "-e", "DB_PRIMARY_URL=", "url-service", check=False, timeout=60
    )
    assert result.returncode != 0
    assert "DB_PRIMARY_URL" in result.stdout + result.stderr


@scenario("service-health", "Liveness ignores database state")
@scenario("service-health", "Database unavailable")
def test_database_outage(restore, http):
    compose("stop", "postgres-primary")
    wait_until(lambda: ready_status() == 503, timeout=20, message="readiness 503")
    body = http.get("/health/ready").json()
    assert body["status"] == "DOWN" and body["components"]["postgresPrimary"] == "DOWN"
    assert http.get("/health/live").status_code == 200

    compose("start", "postgres-primary")
    wait_until(lambda: ready_status() == 200, timeout=60, message="readiness back to 200")


@scenario("service-health", "Scale out")
def test_scale_out_spreads_requests(restore):
    compose("up", "-d", "--no-recreate", "--wait", "--scale", "url-service=3", timeout=240)
    ips = {container_ip(c) for c in container_ids("url-service")}
    assert len(ips) == 3

    def three_upstreams_used():
        since = utc_now()
        for _ in range(30):
            httpx.get(f"{BASE_URL}/health/ready", timeout=5)
        ups = {ln["up"].split(":")[0] for ln in logs_since("nginx", since) if ln.get("up")}
        return ips <= ups

    # Nginx re-resolves Docker DNS every 10 s, so allow time for the new replica to appear.
    wait_until(three_upstreams_used, timeout=40, interval=2, message="3 distinct upstreams")


@scenario("service-health", "Replica failure")
def test_requests_survive_a_killed_replica(restore):
    # GIVEN 2 replicas and Nginx routing only to them (it may still hold addresses of
    # replicas removed by an earlier test's cleanup until its next DNS refresh).
    current = {container_ip(c) for c in container_ids("url-service")}
    assert len(current) == 2

    def nginx_uses_only_current_replicas():
        since = utc_now()
        for _ in range(20):
            httpx.get(f"{BASE_URL}/health/live", timeout=10)
        ups = {ln["up"].split(":")[0] for ln in logs_since("nginx", since) if ln.get("up")}
        return ups == current

    wait_until(nginx_uses_only_current_replicas, timeout=30, interval=1, message="steady state")

    victim = container_ids("url-service")[0]
    docker("kill", victim)
    statuses = [httpx.get(f"{BASE_URL}/health/live", timeout=10).status_code for _ in range(10)]
    assert statuses == [200] * 10


@scenario("service-health", "Stop during traffic")
def test_graceful_shutdown_finishes_in_flight_request(restore, test_routes_enabled):
    target = container_ids("url-service")[0]
    ip = container_ip(target)

    def from_nginx(path: str) -> subprocess.Popen:
        # Call the replica directly (bypassing load balancing) from inside the nginx container.
        return subprocess.Popen(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "nginx",
                "wget",
                "-S",
                "-qO-",
                "-T",
                "20",
                f"http://{ip}:8000{path}",
            ],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    in_flight = from_nginx("/api/v1/__test/slow?seconds=2")
    time.sleep(0.5)
    started = time.monotonic()
    stopper = subprocess.Popen(["docker", "stop", "-t", "15", target], stdout=subprocess.DEVNULL)

    time.sleep(0.8)  # inside the 2 s drain window
    probe = from_nginx("/health/ready")
    probe_out, _ = probe.communicate(timeout=10)
    assert "503" in probe_out, f"readiness during drain: {probe_out!r}"

    out, _ = in_flight.communicate(timeout=20)
    assert '"slept":2.0' in out and "200 OK" in out, f"in-flight request: {out!r}"

    stopper.wait(timeout=20)
    elapsed = time.monotonic() - started
    state = inspect(target)["State"]
    assert state["ExitCode"] == 0, f"exit code {state['ExitCode']}"
    assert elapsed < 15, f"took {elapsed:.1f}s"
