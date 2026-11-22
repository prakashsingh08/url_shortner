# Project Context

## Purpose
A URL shortener platform built as a distributed-systems learning project: create short links, redirect with low latency, manage links, collect click analytics, and evolve from a functional MVP to a horizontally scaled, fault-tolerant system.

Source of truth for intent: `docs/requirements/requirements-v1.md`. Architecture: `docs/hld/hld-v1.md`. Detailed design: `docs/lld/lld-v1.md`. Delivery order: `docs/features/features-v1.md`.

## Tech Stack
- **Application language/framework:** Python 3.14 + FastAPI, kept basic (decided 2026-11-22):
  - Web: `fastapi`, `uvicorn` (1 process per container; scale with Compose replicas)
  - Config: `pydantic-settings` (env vars only)
  - PostgreSQL: `psycopg` 3 + `psycopg_pool` with plain SQL (no ORM)
  - Logging: stdlib `logging` with a small JSON formatter
  - Tests: `pytest`, `httpx`; lint/format: `ruff`
  - Dependencies: pinned `requirements.txt` / `requirements-dev.txt`; local `.venv/`
  - Further libraries (Redis, Kafka, ClickHouse clients) are chosen by the change that needs them
- **Edge:** Nginx 1.27.3+ (Docker DNS `resolve` upstreams)
- **System of record:** PostgreSQL 16, migrations via Flyway (one-shot container)
- **Cache / counters:** Redis 7.4 (from `add-url-caching`)
- **Events:** Apache Kafka 3.8 (KRaft, single broker locally) (from `add-click-analytics`)
- **Analytics store:** ClickHouse 24.8 (from `add-click-analytics`)
- **Observability:** Prometheus, Grafana (from `add-observability`)
- **Runtime:** Docker Compose (local only); profiles `cache`, `analytics`, `ha`, `obs`

## Project Conventions

### Code Style
- Layered modules: `api → application → domain`; `infrastructure` implements ports declared in `application`/`domain`. Domain code never imports infrastructure.
- Configuration only through environment variables (LLD §9.3); no config files baked into images.
- JSON fields in `camelCase`; timestamps ISO-8601 UTC with milliseconds.

### Architecture Patterns
- Stateless service instances; any instance can be killed or scaled at any time.
- The redirect path does only cache lookup, status check and redirect; all other work is asynchronous.
- Optional dependencies (Redis, Kafka, ClickHouse, replica) degrade behaviour, never correctness or readiness.

### Testing Strategy
- Every spec scenario has at least one automated test that names the scenario.
- Unit tests for pure logic; integration tests against real containers (same images as Compose); API tests against the full stack through Nginx on `http://localhost:8080`.
- A change is done only when its scenarios pass on the local Docker Compose stack.

### Git Workflow
- Start every feature with `/add-url-shortner-feature <number> <description>` (`.claude/skills/add-url-shortner-feature/`).
- The working tree must be clean before a feature starts. Branch per feature: `feature/<change-id>`, created from `main`.
- Commit messages: `<change-id>: <imperative summary>`.
- Archive the change on the feature branch, then merge to `main`.

## Domain Context
- **Short code:** 7-char Base62 generated code, or a 4–32 char custom alias.
- **Effective status:** `ACTIVE | EXPIRED | DELETED | DISABLED`; `EXPIRED` is computed from `expiresAt`, never stored.
- Short codes are never reused, even after deletion.

## Important Constraints
- `CODE_PERMUTATION_KEY` must never change once any URL exists.
- Raw client IPs are never persisted (only in transient Kafka events).
- Original URLs are never logged in full (host only).
- Must run on a single developer laptop (Docker Desktop ≥ 6 GB).

## External Dependencies
- Optional MaxMind GeoLite2 Country database for geo analytics (not committed).
