# Change: Add platform foundation (F-01)

## Why
Every later feature needs a runnable service, a database with migrations, an edge proxy, a consistent error format and a test harness. Building these once, first, means each later change only adds behaviour. It also satisfies AC-10 ("the application can run using Docker") from day one.

## What Changes
- **url-service skeleton**: layered module structure (LLD §2), env-based configuration, JSON structured logging, graceful shutdown.
- **Health probes**: `GET /health/live` and `GET /health/ready` with per-dependency component status.
- **API conventions**: RFC 9457 problem-details errors, `X-Request-Id` propagation, 404 for unknown API routes, 400 for malformed JSON, 500 without internals.
- **API documentation**: OpenAPI schema at `/api/openapi.json` and interactive docs at `/api/docs` (moved from FastAPI's default `/docs`, which clashes with the short-code path space).
- **Docker Compose core profile**: `nginx`, `postgres-primary`, `migrate` (Flyway), `url-service` × 2 replicas; health-checked startup order.
- **Nginx edge**: routes `/api/`, `/health/` and short-code-shaped paths to the service; rejects everything else with 404; hides `/metrics`; per-IP request limit; re-resolves scaled replicas.
- **Database baseline**: Flyway wired up with a `V1__baseline.sql` migration (creates `system_settings`, used by later changes).
- **Developer tooling**: `.env.example`, `.gitignore`, `Makefile`, test harness (unit + integration + API tests against the Compose stack).

No business features (auth, URLs, redirects) are included.

## Impact
- Requirements covered: NFR-06 (modular design, structured logging, automated tests, documented APIs), AC-10 (runs on Docker), requirements §10 (error response format)
- Affected specs: `service-health` (new), `api-conventions` (new)
- Affected code: `docker-compose.yml`, `infra/nginx/`, `infra/postgres/`, `services/url-service/`, `Makefile`, `.env.example`
- Docs: no HLD/LLD changes expected. LLD §9.3 is implemented for the core profile only; other profiles are added by later changes.
- Stack: **Python 3.14 + FastAPI**, basic setup (see `openspec/project.md`).
