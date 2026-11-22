"""Helpers for black-box tests against the running Docker Compose stack."""

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[4]
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{os.environ.get('HTTP_PORT', '8080')}")


def compose(*args: str, check: bool = True, timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def docker(*args: str, check: bool = True, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


def container_ids(service: str) -> list[str]:
    return compose("ps", "-a", "-q", service).stdout.split()


def inspect(container_id: str) -> dict:
    return json.loads(docker("inspect", container_id).stdout)[0]


def container_ip(container_id: str) -> str:
    networks = inspect(container_id)["NetworkSettings"]["Networks"]
    return next(iter(networks.values()))["IPAddress"]


def logs_since(service: str, since: str) -> list[dict]:
    """JSON log lines of a service since an RFC 3339 timestamp (non-JSON lines skipped)."""
    out = compose("logs", "--no-log-prefix", "--since", since, service).stdout
    lines = []
    for raw in out.splitlines():
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    return lines


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1))


def wait_until(predicate, timeout: float = 30, interval: float = 0.5, message: str = "condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (httpx.HTTPError, subprocess.SubprocessError) as exc:
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {message} (last: {last!r})")


def ready_status() -> int:
    return httpx.get(f"{BASE_URL}/health/ready", timeout=5).status_code


def restore_stack(replicas: int = 2) -> None:
    """Bring the core stack back to its normal shape after a disruptive test."""
    compose("start", "postgres-primary", check=False)
    compose("up", "-d", "--wait", "--scale", f"url-service={replicas}", timeout=240)
    wait_until(lambda: ready_status() == 200, timeout=60, message="stack ready again")
