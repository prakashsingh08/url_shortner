# Design: add-platform-foundation

## Context
First change in the repo; nothing exists yet. The target is LLD §9 restricted to the `core` profile, so the system is runnable and horizontally scalable before any feature lands.

## Goals / Non-Goals
- **Goals:** reproducible one-command local stack; scaled replicas behind Nginx; consistent errors/logging; a test harness later changes reuse.
- **Non-Goals:** Redis, Kafka, ClickHouse, replica, Prometheus (later changes); authentication; any URL behaviour.

## Decisions

### D1. Compose core profile only
Services: `nginx`, `postgres-primary`, `migrate`, `url-service`. Optional services and their `depends_on … required: false` entries are added by the changes that introduce them, rather than stubbed now. This keeps the file honest: everything in it actually runs.

### D2. Readiness depends on PostgreSQL only
`/health/ready` = 200 when the primary answers `SELECT 1` within 500 ms and the instance is not shutting down. Optional dependencies are reported as `DISABLED` (not configured) in this change. Later changes add `UP`/`DOWN` reporting for them, without affecting readiness (LLD §3.10).

### D3. Baseline migration
`V1__baseline.sql` creates `system_settings` (LLD §4.1). The LLD's V1–V5 numbering is replaced by **feature-ordered migrations**: each change adds the next `V<n>__<change>.sql`. Flyway ordering then matches delivery order. The LLD schema remains the target end-state.

| Version | Introduced by |
|---|---|
| V1__baseline | add-platform-foundation |
| V2__users | add-user-auth |
| V3__urls_id_blocks_idempotency | add-url-shortening |
| V4__reserved_aliases | add-custom-alias |
| … | later changes |

### D4. Nginx routing
Implements the LLD §9.5 template with only `/api/`, `/health/` and the code-shaped regex location. During this change, code-shaped paths reach the service and return the standard 404 problem (no redirect logic yet). That proves the routing works before F-04.

### D5. Test harness layout
```
services/url-service/tests/
├── unit/          # pure logic, no I/O
├── integration/   # real Postgres via Testcontainers (or compose -p urlsh-test)
└── api/           # black-box HTTP against BASE_URL (default http://localhost:8080)
```
API tests are named after spec scenarios (e.g. `service-health: Readiness reports unavailable database`), so scenario coverage is visible in test output.

### D6. Request ID
Nginx generates `$request_id` and sends it as `X-Request-Id`. The service uses an incoming `X-Request-Id` when it matches `^[A-Za-z0-9-]{8,64}$`, otherwise it generates one (direct calls bypassing Nginx). The ID is echoed in the response header, error bodies and every log line.

### D7. Python + FastAPI, basic setup
- One `uvicorn` process per container, no Gunicorn and no multiple workers: Compose replicas provide the parallelism, which keeps "one container = one instance" true for scaling tests.
- Async endpoints with an async `psycopg_pool` and plain SQL in repository classes, no ORM. Queries stay visible, and the LLD's SQL is used as written.
- Errors: a custom `AppException` hierarchy plus FastAPI exception handlers that render problem+json. FastAPI's default `422` for request validation is overridden to `400 VALIDATION_ERROR` (LLD §7.1).
- Docker image `python:3.14-slim` has no `wget`, so container health checks use `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"` instead of the `wget` command in LLD §9.3.

```
services/url-service/
├── Dockerfile
├── requirements.txt / requirements-dev.txt
├── app/
│   ├── main.py            # FastAPI app factory, lifespan (pool open/close, shutdown flag)
│   ├── config.py          # pydantic-settings
│   ├── api/               # routers, error handlers, middleware
│   ├── application/       # use-case services
│   ├── domain/            # entities, errors
│   └── infrastructure/    # db pool, repositories, logging
└── tests/{unit,integration,api}/
```

## Risks / Trade-offs
- **2 replicas from day one** costs a little memory but catches accidental in-process state early.

## Open Questions
- None.
