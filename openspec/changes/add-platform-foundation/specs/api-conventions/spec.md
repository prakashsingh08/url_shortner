## ADDED Requirements

### Requirement: Problem Details Error Format
Every error response from url-service SHALL use `Content-Type: application/problem+json` with the fields `type`, `title`, `status`, `code`, `detail`, `instance` and `requestId` (RFC 9457 plus `code` and `requestId`). Validation errors SHALL additionally include `errors[]` entries with `field` and `message`.

#### Scenario: Error body shape
- **WHEN** any request results in an error response
- **THEN** the `Content-Type` is `application/problem+json`
- **AND** the body contains `type`, `title`, `status`, `code`, `detail`, `instance` and `requestId`
- **AND** `status` equals the HTTP status code

### Requirement: Unknown Routes
The system SHALL respond `404` with code `NOT_FOUND` for API paths and methods that do not exist.

#### Scenario: Unknown API path
- **WHEN** a client sends `GET /api/v1/does-not-exist`
- **THEN** the response status is `404`
- **AND** the body `code` is `NOT_FOUND`

### Requirement: Malformed Request Bodies
The system SHALL respond `400` with code `VALIDATION_ERROR` when a JSON request body cannot be parsed, and SHALL reject request bodies larger than 16 KB with `413`.

#### Scenario: Invalid JSON
- **WHEN** a client sends a request to a JSON endpoint with body `{"broken":`
- **THEN** the response status is `400`
- **AND** the body `code` is `VALIDATION_ERROR`

#### Scenario: Oversized body
- **WHEN** a client sends a 20 KB body to any `/api/` endpoint
- **THEN** the response status is `413`

### Requirement: No Internal Details in Errors
Unexpected failures SHALL return `500` with code `INTERNAL_ERROR` and a generic `detail`. Stack traces, SQL, hostnames and exception messages SHALL NOT appear in responses, and the full error SHALL be logged with the same `requestId`.

#### Scenario: Unhandled exception
- **GIVEN** a handler throws an unexpected exception (test-only route enabled with `APP_ENV=test`)
- **WHEN** a client calls it
- **THEN** the response status is `500` with code `INTERNAL_ERROR`
- **AND** the body contains no stack trace or exception message
- **AND** an `ERROR` log line with the same `requestId` contains the stack trace

### Requirement: Request ID Propagation
Every response SHALL carry an `X-Request-Id` header. The service SHALL reuse an incoming `X-Request-Id` matching `^[A-Za-z0-9-]{8,64}$` and otherwise generate a new one. The same ID SHALL appear in error bodies and in every log line written for that request.

#### Scenario: ID supplied by the edge
- **WHEN** a request arrives through Nginx
- **THEN** the response `X-Request-Id` equals the ID Nginx generated
- **AND** the service's log lines for that request contain the same `requestId`

#### Scenario: Invalid incoming ID replaced
- **WHEN** a request arrives directly at the service with `X-Request-Id: <script>`
- **THEN** the response carries a newly generated `X-Request-Id` matching `^[A-Za-z0-9-]{8,64}$`

### Requirement: Structured Logging
url-service SHALL write one JSON object per log line to stdout, containing at least `ts`, `level`, `service`, `instance`, `requestId` (when in a request) and `msg`, and SHALL log each completed API request with `method`, `route`, `status` and `latencyMs`. Logs SHALL NOT contain passwords, tokens or full request bodies.

#### Scenario: Request log line
- **WHEN** a client calls `GET /health/ready`
- **THEN** stdout contains a JSON line with `method":"GET"`, `route":"/health/ready"`, the response `status`, a numeric `latencyMs` and the request's `requestId`
