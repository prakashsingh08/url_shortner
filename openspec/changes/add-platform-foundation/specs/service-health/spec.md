## ADDED Requirements

### Requirement: Liveness Probe
The system SHALL expose `GET /health/live`, which returns `200` with body `{"status":"UP"}` while the process is running, without checking any dependency.

#### Scenario: Process is running
- **WHEN** a client sends `GET /health/live`
- **THEN** the response status is `200`
- **AND** the body is `{"status":"UP"}`

#### Scenario: Liveness ignores database state
- **GIVEN** PostgreSQL is stopped
- **WHEN** a client sends `GET /health/live`
- **THEN** the response status is `200`

### Requirement: Readiness Probe
The system SHALL expose `GET /health/ready`, which returns `200` only when the PostgreSQL primary answers a trivial query within 500 ms and the instance is not shutting down, and `503` otherwise. The body SHALL report each dependency as `UP`, `DOWN` or `DISABLED`.

#### Scenario: Database reachable
- **GIVEN** PostgreSQL is running
- **WHEN** a client sends `GET /health/ready`
- **THEN** the response status is `200`
- **AND** the body contains `"status":"UP"` and `"components":{"postgresPrimary":"UP", ...}`

#### Scenario: Database unavailable
- **GIVEN** PostgreSQL is stopped
- **WHEN** a client sends `GET /health/ready`
- **THEN** the response status is `503`
- **AND** the body contains `"status":"DOWN"` and `"postgresPrimary":"DOWN"`

#### Scenario: Optional dependencies not configured
- **GIVEN** Redis, Kafka, ClickHouse and the read replica are not configured
- **WHEN** a client sends `GET /health/ready`
- **THEN** those components are reported as `DISABLED`
- **AND** they do not affect the response status

### Requirement: One-Command Local Stack
The system SHALL start with `docker compose up` (core profile) from a clean clone plus a `.env` copied from `.env.example`. Database migrations SHALL complete before any url-service instance starts, and the platform SHALL be reachable at `http://localhost:8080`.

#### Scenario: Clean start
- **GIVEN** no containers or volumes exist
- **WHEN** the developer runs `make up`
- **THEN** `nginx`, `postgres-primary` and every `url-service` replica report healthy
- **AND** `migrate` exits with code 0 before any `url-service` container starts
- **AND** `GET http://localhost:8080/health/ready` returns `200`

#### Scenario: Missing required configuration
- **GIVEN** `DB_PRIMARY_URL` is not set
- **WHEN** a url-service container starts
- **THEN** it exits with a non-zero code
- **AND** its log names the missing variable

### Requirement: Horizontal Scaling Behind the Edge
The system SHALL run url-service as multiple stateless replicas behind Nginx. Nginx SHALL discover replicas added or removed with `docker compose up --scale url-service=N` without restarting, and SHALL distribute requests across them.

#### Scenario: Scale out
- **GIVEN** the stack is running with 2 url-service replicas
- **WHEN** the developer runs `make scale N=3` and sends 30 requests to `/health/ready`
- **THEN** the Nginx access log shows requests served by 3 distinct upstream addresses

#### Scenario: Replica failure
- **GIVEN** the stack is running with 2 url-service replicas
- **WHEN** one replica container is killed
- **THEN** subsequent `GET` requests through Nginx succeed, served by the remaining replica

### Requirement: Edge Routing
Nginx SHALL forward only `/api/…`, `/health/…` and paths matching `^/[A-Za-z0-9_-]{4,32}$` to url-service, SHALL answer all other paths with `404` itself, and SHALL NOT expose `/metrics` publicly.

#### Scenario: Short-code-shaped path reaches the service
- **WHEN** a client sends `GET /abcd123`
- **THEN** the request is handled by url-service (the response carries an `X-Request-Id` and a problem-details body)

#### Scenario: Other paths rejected at the edge
- **WHEN** a client sends `GET /a/b/c` or `GET /ab`
- **THEN** Nginx responds `404` without contacting url-service

#### Scenario: Metrics hidden
- **WHEN** a client sends `GET /metrics` through Nginx
- **THEN** the response status is `404`

### Requirement: Graceful Shutdown
On `SIGTERM`, a url-service instance SHALL start returning `503` from `/health/ready`, finish in-flight requests for up to 10 seconds, release its database connections and exit with code 0.

#### Scenario: Stop during traffic
- **GIVEN** a request taking 2 seconds is in flight on an instance
- **WHEN** that instance receives `SIGTERM`
- **THEN** the in-flight request completes successfully
- **AND** the instance exits with code 0 within 15 seconds
