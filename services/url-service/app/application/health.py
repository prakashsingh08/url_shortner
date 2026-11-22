"""Readiness use case (service-health: Readiness Probe, design D2)."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ComponentState(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    DISABLED = "DISABLED"


class DatabaseProbe(Protocol):
    async def is_up(self) -> bool: ...


# Dependencies that later changes introduce. Reported, never used for readiness.
OPTIONAL_COMPONENTS = ("postgresReplica", "redis", "kafka", "clickhouse")


@dataclass
class Readiness:
    ready: bool
    components: dict[str, ComponentState]

    def body(self) -> dict:
        return {
            "status": "UP" if self.ready else "DOWN",
            "components": {k: v.value for k, v in self.components.items()},
        }


class HealthService:
    def __init__(self, primary: DatabaseProbe, is_shutting_down):
        self.primary = primary
        self.is_shutting_down = is_shutting_down

    async def readiness(self) -> Readiness:
        primary_up = await self.primary.is_up()
        components = {"postgresPrimary": ComponentState.UP if primary_up else ComponentState.DOWN}
        components.update({name: ComponentState.DISABLED for name in OPTIONAL_COMPONENTS})
        ready = primary_up and not self.is_shutting_down()
        return Readiness(ready=ready, components=components)
