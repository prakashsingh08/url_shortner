import asyncio

from app.application.health import HealthService
from tests.markers import scenario


class FakeProbe:
    def __init__(self, up: bool):
        self.up = up

    async def is_up(self) -> bool:
        return self.up


def readiness(up: bool, shutting_down: bool = False):
    return asyncio.run(HealthService(FakeProbe(up), lambda: shutting_down).readiness())


def test_ready_when_database_up():
    r = readiness(True)
    assert r.ready and r.body()["status"] == "UP"
    assert r.body()["components"]["postgresPrimary"] == "UP"


def test_not_ready_when_database_down():
    r = readiness(False)
    assert not r.ready and r.body()["status"] == "DOWN"
    assert r.body()["components"]["postgresPrimary"] == "DOWN"


def test_not_ready_while_shutting_down():
    r = readiness(True, shutting_down=True)
    assert not r.ready and r.body()["components"]["postgresPrimary"] == "UP"


@scenario("service-health", "Optional dependencies not configured")
def test_optional_dependencies_reported_disabled_and_ignored():
    r = readiness(True)
    comps = r.body()["components"]
    for name in ("postgresReplica", "redis", "kafka", "clickhouse"):
        assert comps[name] == "DISABLED"
    assert r.ready
