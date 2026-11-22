"""PostgreSQL connection pool (psycopg 3, plain SQL, no ORM)."""

import asyncio
import logging

from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)


def create_primary_pool(conninfo: str, max_size: int) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        open=False,
        # Validate a connection before handing it out, so a database restart
        # does not leave broken connections in the pool.
        check=AsyncConnectionPool.check_connection,
        name="primary",
    )


class PostgresProbe:
    """Implements the readiness DatabaseProbe port against a connection pool."""

    def __init__(self, pool: AsyncConnectionPool | None, timeout_seconds: float):
        self.pool = pool
        self.timeout = timeout_seconds

    async def is_up(self) -> bool:
        if self.pool is None:
            return False
        try:
            async with asyncio.timeout(self.timeout):
                async with self.pool.connection(timeout=self.timeout) as conn:
                    await conn.execute("SELECT 1")
            return True
        except Exception as exc:  # any failure means "not ready"
            log.warning(
                "database probe failed",
                extra={"fields": {"dependency": "postgresPrimary", "error": type(exc).__name__}},
            )
            return False
