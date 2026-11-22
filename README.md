# URL Shortener Platform

A distributed-systems learning project: a URL shortener built feature by feature with spec-driven development (OpenSpec), running locally on Docker Compose.

| Doc | What it covers |
|---|---|
| [Requirements](docs/requirements/requirements-v1.md) | What the platform must do |
| [HLD](docs/hld/hld-v1.md) | Architecture, capacity, data stores, caching, scaling |
| [LLD](docs/lld/lld-v1.md) | API contracts, schemas, algorithms, error handling, Compose |
| [Feature roadmap](docs/features/features-v1.md) | 14 features, build order and status |
| [OpenSpec](openspec/) | Specs (`specs/`) and in-progress changes (`changes/`) |

**Status:** F-01 platform foundation (service skeleton, Nginx, PostgreSQL, health probes, error format, logging, tests). No URL shortening yet.

## Prerequisites

- Docker Desktop with Compose v2 (≥ 6 GB memory for later features)
- Python 3.14 (for running tests locally)
- `make`

## Quick start

```bash
make install              # create .venv and install Python dependencies
make up                   # creates .env from .env.example on first run, builds, starts, waits until healthy
```

The platform runs at `http://localhost:${HTTP_PORT}`. `HTTP_PORT` is set in `.env` and defaults to 8080. If something else already uses 8080, set `HTTP_PORT=8081` (or any free port) in `.env`.

| URL | What |
|---|---|
| `/health/live` | Liveness probe |
| `/health/ready` | Readiness probe with per-dependency status |
| `/api/docs` | Interactive API docs (Swagger UI) |
| `/api/openapi.json` | OpenAPI schema |

```bash
curl -i http://localhost:8080/health/ready
```

## Services (core profile)

| Service | Image | Notes |
|---|---|---|
| `nginx` | `nginx:1.27.3-alpine` | Edge on `${HTTP_PORT}`; routes `/api/`, `/health/` and short-code paths; discovers scaled replicas through Docker DNS |
| `url-service` | built from `services/url-service` | Python 3.14 + FastAPI, 2 replicas, no host port |
| `postgres-primary` | `postgres:16.4` | `localhost:5432`, database `urlshortener`, user `app` |
| `migrate` | `flyway/flyway:10.17.3` | One-shot; applies `infra/postgres/migrations` before the service starts |

## Common commands

```bash
make help                 # list all targets
make ps                   # container status
make logs S=url-service   # follow one service's JSON logs
make scale N=3            # run 3 url-service replicas
make psql                 # psql on the primary
make down                 # stop (keep data)
make nuke                 # stop and delete data volumes
```

## Tests

```bash
make test-unit            # unit tests, no Docker needed
make test-integration     # against the Compose PostgreSQL (needs `make up`)
make test                 # both of the above
make test-api             # restarts the stack with APP_ENV=test, then black-box tests through Nginx
make test-api ARGS="-k graceful -v"   # a subset
make lint                 # ruff lint + format check
```

`make test-api` is **disruptive**: it stops PostgreSQL, kills and scales replicas, and restores the stack after each test (about 1 minute).

Every OpenSpec scenario is linked to at least one test with `@scenario("<capability>", "<scenario>")`. `tests/unit/test_scenario_coverage.py` fails if a scenario has no test.

**Known issue:** for about 2 s after a replica is killed, one request can fail with 504. The `Replica failure` test is marked `xfail`, and the fix is planned for F-14. See `openspec/changes/add-platform-foundation/design.md` D10.

## Development workflow

Features are built one at a time with the project command:

```
/add-url-shortner-feature <number> <description>
```

It checks for a clean git tree, creates `feature/<change-id>`, validates or writes the OpenSpec change, asks for plan approval, then implements and verifies it on Docker Compose. See [`openspec/AGENTS.md`](openspec/AGENTS.md).
