# Tasks: add-platform-foundation

## 1. Decisions & repo setup
- [x] 1.1 Choose application language/framework → **Python 3.14 + FastAPI (basic)**; recorded in `openspec/project.md` and LLD §1
- [x] 1.2 Extend the existing `.gitignore` (add build outputs, test caches, IDE files) and `.env.example` (core-profile variables from LLD §9.4)
- [x] 1.3 Add `requirements.txt` / `requirements-dev.txt` (pinned) and install them into `.venv`
- [x] 1.4 Add `Makefile` targets: `up`, `down`, `nuke`, `logs`, `ps`, `psql`, `scale`, `test`, `test-api`

## 2. Database
- [x] 2.1 Add `infra/postgres/migrations/V1__baseline.sql` creating `system_settings`
- [x] 2.2 Add `postgres-primary` and `migrate` (Flyway) services to `docker-compose.yml` with health checks
- [x] 2.3 Verify: `docker compose up migrate` exits 0 and `flyway_schema_history` shows V1

## 3. url-service skeleton
- [x] 3.1 Create `services/url-service/` with `api / application / domain / infrastructure` modules and a `Dockerfile` based on `python:3.14-slim` (non-root user; health check via `python -c` + `urllib`, no `wget` needed)
- [x] 3.2 Configuration loader: read and validate env vars at startup; fail fast with a clear message on missing required ones
- [x] 3.3 PostgreSQL connection pool (`DB_PRIMARY_URL`, `DB_POOL_SIZE_WRITE`)
- [x] 3.4 JSON structured logging with `requestId` in the log context; logging rules from LLD §7.4
- [x] 3.5 Request-ID middleware (design D6)
- [x] 3.6 Problem-details error handler: `AppException` hierarchy, malformed JSON → 400, unknown route → 404, unexpected → 500 (LLD §7.2)
- [x] 3.7 `GET /health/live` and `GET /health/ready` (design D2)
- [x] 3.7a Serve OpenAPI at `/api/openapi.json` and docs at `/api/docs`; disable `/docs`, `/redoc`, `/openapi.json`
- [x] 3.8 Graceful shutdown on SIGTERM (LLD §7.5, minus the click-queue flush)

## 4. Edge & orchestration
- [x] 4.1 Add `infra/nginx/templates/default.conf.template` (design D4)
- [x] 4.2 Add `nginx` and `url-service` (2 replicas, no host port) to `docker-compose.yml`
- [x] 4.3 Verify: `make up` from a clean clone → all services healthy; `curl localhost:8080/health/ready` → 200
- [x] 4.4 Verify: `make scale N=3` → requests are spread across 3 instances (check the `upstream` field in Nginx access logs)

## 5. Tests
- [x] 5.1 Unit tests: config validation, request-ID acceptance rules, error mapping
- [x] 5.2 Integration test: readiness against a real Postgres (up → 200, stopped → 503)
- [x] 5.3 API tests for every scenario in `specs/service-health` and `specs/api-conventions`
- [x] 5.4 `make test` runs unit + integration; `make test-api` runs API tests against the running stack

## 6. Wrap-up
- [x] 6.1 Root `README.md`: prerequisites, `cp .env.example .env`, `make up`, URLs, how to run tests
- [ ] 6.2 Update `docs/features/features-v1.md` status; archive the change
