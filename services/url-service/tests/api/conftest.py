import httpx
import pytest

from tests.api.stack import BASE_URL, ready_status, wait_until


@pytest.fixture(scope="session", autouse=True)
def stack_is_running():
    # Right after `compose up`, Nginx may briefly still hold old replica addresses.
    try:
        wait_until(lambda: ready_status() == 200, timeout=30, message="stack ready")
    except AssertionError as exc:
        pytest.exit(f"Stack not ready at {BASE_URL} ({exc}). Run `make test-api`.", returncode=2)


@pytest.fixture(scope="session")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        yield client


@pytest.fixture(scope="session")
def test_routes_enabled(http):
    if http.post("/api/v1/__test/echo", json={"message": "hi"}).status_code != 200:
        pytest.fail("test-only routes are off: start the stack with APP_ENV=test (make test-api)")
