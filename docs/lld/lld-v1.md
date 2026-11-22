# URL Shortener Platform
## Low-Level Design (LLD) Document

**Document Version:** 1.0  
**Status:** Draft – Pending Design Review  
**Document Type:** LLD / Detailed Design  
**Source Documents:** [requirements-v1.md](../requirements/requirements-v1.md), [hld-v1.md](../hld/hld-v1.md)  
**Target Environment:** Local Docker Compose (single host)

---

## 1. Purpose and Scope

This document turns the HLD into buildable detail: API contracts, database and cache schemas, class structure, algorithms, error handling, and the exact Docker Compose environment the system runs in locally.

**Implementation stack:** Python 3.14 + FastAPI (decided 2026-11-22; details in `openspec/project.md`). Class diagrams and pseudocode still use neutral types (`String`, `Long`, `Instant`, `Optional<T>`, `Result`). Everything else (SQL, Redis keys, Kafka payloads, HTTP contracts, Compose files) is final and language-independent.

### 1.1 Changes vs HLD

The LLD refines the HLD in these places. The HLD has been updated to match.

| # | HLD said | LLD decision | Reason |
|---|---|---|---|
| 1 | Idempotency keys in Redis | **Idempotency keys in PostgreSQL**, in the same transaction as the URL insert | Atomic with the insert; survives Redis outage or LRU eviction (NFR-04). |
| 2 | API path `/api/v1/urls/{id}` | `{id}` **is the short code** | Avoids exposing internal sequential IDs. |
| 3 | Non-owner → 403 | Non-owner on a user resource → **404**; 403 only for role checks (admin endpoints) | Prevents probing which codes exist and who owns them. |
| 4 | Referrer stored without query string | Only the **referrer host** is stored | Lower cardinality for rollups; less personal data. |
| 5 | Open item: MVP PG click table vs Kafka | **Kafka + ClickHouse from the start**, behind the `analytics` Compose profile | One analytics path to build; profile keeps laptop footprint small when not needed. |
| 6 | Redis listed as holding ID blocks | **ID blocks live only in PostgreSQL** | Durable; one fewer dependency on the write path. |

---

## 2. Repository and Module Layout

```
url_shortner/
├── docker-compose.yml
├── .env.example
├── Makefile
├── services/
│   ├── url-service/                # HTTP API + redirect (stateless, N replicas)
│   │   ├── Dockerfile
│   │   └── src/
│   │       ├── api/                # controllers, request/response DTOs, error mapper
│   │       ├── application/        # use-case services (UrlService, RedirectService, ...)
│   │       ├── domain/             # entities, value objects, domain errors
│   │       └── infrastructure/     # postgres, redis, kafka, clickhouse, id-gen, config
│   └── analytics-worker/           # Kafka consumer → ClickHouse
│       ├── Dockerfile
│       └── src/
├── infra/
│   ├── nginx/templates/default.conf.template
│   ├── postgres/
│   │   ├── primary/01-replication.sh
│   │   ├── replica/entrypoint.sh
│   │   └── migrations/V1__init.sql … V5__seed.sql
│   ├── clickhouse/init/01-schema.sql
│   ├── prometheus/prometheus.yml
│   └── grafana/provisioning/{datasources,dashboards}/
├── loadtest/k6/{redirect.js,create.js,mixed.js}
└── docs/{requirements,hld,lld}/
```

Dependency rule inside `url-service`: `api → application → domain`; `infrastructure` implements interfaces declared in `application`/`domain`. Domain code never imports infrastructure.

---

## 3. API Contracts

### 3.1 Conventions

| Item | Rule |
|---|---|
| Base URL (local) | `http://localhost:8080` (Nginx). Short URLs look like `http://localhost:8080/aB91xK7`. |
| Content type | `application/json; charset=utf-8`; errors use `application/problem+json` (RFC 9457). |
| Auth | `Authorization: Bearer <JWT>` on all `/api/v1/**` except `/auth/register` and `/auth/login`. |
| Timestamps | ISO-8601 UTC with `Z`, millisecond precision. |
| Field naming | `camelCase`. |
| Request ID | Nginx sets `X-Request-Id`; echoed in every response and in error bodies. |
| Pagination | Keyset: `?limit=20&cursor=<opaque>`; response carries `nextCursor` (`null` at the end). `limit` max 100. |
| Idempotency | `POST /api/v1/urls` accepts `Idempotency-Key` (1–64 chars, `[A-Za-z0-9_-]`). Replays return header `Idempotent-Replayed: true`. |
| Rate-limit headers | `RateLimit-Limit`, `RateLimit-Remaining`, and on 429 `Retry-After` (seconds). |
| Versioning | URI prefix `/api/v1`; breaking changes → `/api/v2`. |

### 3.2 Endpoint Summary

| Method | Path | Auth | Purpose | Success |
|---|---|---|---|---|
| POST | `/api/v1/auth/register` | — | Create account | 201 |
| POST | `/api/v1/auth/login` | — | Get access token | 200 |
| GET | `/api/v1/auth/me` | User | Current user | 200 |
| POST | `/api/v1/urls` | User | Create short URL | 201 |
| GET | `/api/v1/urls` | User | List my URLs | 200 |
| GET | `/api/v1/urls/{code}` | User (owner) | URL details | 200 |
| DELETE | `/api/v1/urls/{code}` | User (owner) | Soft-delete URL | 204 |
| GET | `/api/v1/urls/{code}/analytics` | User (owner) | Aggregated analytics | 200 |
| GET | `/api/v1/urls/{code}/clicks` | User (owner) | Recent raw clicks (timestamps) | 200 |
| GET / HEAD | `/{code}` | — | Redirect | 302 |
| PATCH | `/api/v1/admin/urls/{code}` | Admin | Disable / re-enable link | 200 |
| GET | `/api/v1/admin/urls` | Admin | Search links | 200 |
| POST | `/api/v1/admin/blocked-domains` | Admin | Add to blocklist | 201 |
| DELETE | `/api/v1/admin/blocked-domains/{domain}` | Admin | Remove from blocklist | 204 |
| GET | `/api/v1/admin/stats` | Admin | Platform usage | 200 |
| GET | `/health/live`, `/health/ready` | — | Probes | 200 / 503 |
| GET | `/metrics` | internal only | Prometheus | 200 |

`/metrics` is blocked at Nginx; Prometheus scrapes replicas directly on the Compose network.

### 3.3 Auth

**POST `/api/v1/auth/register`**

```json
{ "email": "alice@example.com", "password": "correct-horse-battery" }
```

- `email`: valid address, ≤ 254 chars, unique case-insensitively.
- `password`: 8–128 chars.

`201 Created`
```json
{ "id": 42, "email": "alice@example.com", "role": "USER", "createdAt": "2026-11-22T10:00:00.000Z" }
```
Errors: `400 VALIDATION_ERROR`, `409 EMAIL_TAKEN`.

**POST `/api/v1/auth/login`**

```json
{ "email": "alice@example.com", "password": "correct-horse-battery" }
```
`200 OK`
```json
{ "accessToken": "eyJhbGciOi...", "tokenType": "Bearer", "expiresIn": 3600 }
```
Errors: `401 INVALID_CREDENTIALS` (same response for unknown email and wrong password), `429 RATE_LIMITED` (5 attempts / minute / IP+email).

JWT claims: `sub` (user id), `role` (`USER`|`ADMIN`), `iat`, `exp`, `jti`. Local: HS256 with `JWT_SECRET`. Production: RS256 with key rotation.

### 3.4 Create Short URL — `POST /api/v1/urls`

Request headers: `Authorization`, optional `Idempotency-Key`.

```json
{
  "originalUrl": "https://example.com/products/electronics/mobile-phones/samsung/galaxy-ultra-256gb",
  "customAlias": "galaxy-ultra",
  "expiresAt": "2027-06-30T00:00:00.000Z"
}
```

| Field | Required | Rules |
|---|---|---|
| `originalUrl` | yes | See §6.4 (http/https only, ≤ 2048 chars, no userinfo, not private/loopback, not our own host, not blocklisted). |
| `customAlias` | no | See §6.5 (`[A-Za-z0-9_-]{4,32}`, not reserved, not in generated namespace). |
| `expiresAt` | no | Must be > now + 1 minute and ≤ now + 5 years. `null`/absent = never expires. |

`201 Created` — header `Location: /api/v1/urls/galaxy-ultra`
```json
{
  "shortCode": "galaxy-ultra",
  "shortUrl": "http://localhost:8080/galaxy-ultra",
  "originalUrl": "https://example.com/products/electronics/mobile-phones/samsung/galaxy-ultra-256gb",
  "isCustom": true,
  "status": "ACTIVE",
  "expiresAt": "2027-06-30T00:00:00.000Z",
  "createdAt": "2026-11-22T10:15:30.123Z"
}
```

Errors: `400 VALIDATION_ERROR | INVALID_URL | URL_SCHEME_NOT_ALLOWED | ALIAS_INVALID | EXPIRY_INVALID`, `401`, `403 URL_BLOCKED`, `409 ALIAS_TAKEN | ALIAS_RESERVED`, `422 IDEMPOTENCY_KEY_REUSED`, `429 RATE_LIMITED`, `503 SERVICE_UNAVAILABLE`.

The same user shortening the same long URL twice gets **two different codes** (no dedup). Retry safety comes from `Idempotency-Key`, not URL dedup.

### 3.5 List My URLs — `GET /api/v1/urls`

Query: `limit` (1–100, default 20), `cursor`, `status` = `active | expired | disabled | all` (default `all`; deleted links never listed).

`200 OK`
```json
{
  "items": [
    {
      "shortCode": "aB91xK7",
      "shortUrl": "http://localhost:8080/aB91xK7",
      "originalUrl": "https://example.com/...",
      "isCustom": false,
      "status": "EXPIRED",
      "expiresAt": "2026-11-01T00:00:00.000Z",
      "createdAt": "2026-10-01T09:00:00.000Z"
    }
  ],
  "nextCursor": "eyJjIjoiMjAyNi0xMC0wMVQwOTowMDowMFoiLCJpIjo5ODc2fQ"
}
```

`status` in responses is the **effective status**: `ACTIVE | EXPIRED | DISABLED | DELETED`. `EXPIRED` is computed (`expiresAt <= now`), never stored. The cursor is base64url of `{"c": createdAt, "i": id}`.

### 3.6 Get / Delete

- **GET `/api/v1/urls/{code}`** → `200` with the same object as in §3.4. Not found / not owner / deleted → `404 URL_NOT_FOUND`.
- **DELETE `/api/v1/urls/{code}`** → `204 No Content`. Idempotent: deleting an already-deleted owned link returns `204`. Not owner → `404`.

### 3.7 Analytics — `GET /api/v1/urls/{code}/analytics`

Query: `from`, `to` (dates `YYYY-MM-DD`, default last 30 days, max range 366 days).

`200 OK`
```json
{
  "shortCode": "aB91xK7",
  "range": { "from": "2026-10-24", "to": "2026-11-22" },
  "totalClicks": 12345,
  "timeseries": [ { "date": "2026-11-21", "clicks": 512 }, { "date": "2026-11-22", "clicks": 87 } ],
  "topCountries": [ { "key": "IN", "clicks": 4000 }, { "key": "ZZ", "clicks": 120 } ],
  "topReferrers": [ { "key": "t.co", "clicks": 2100 }, { "key": "(direct)", "clicks": 900 } ],
  "devices": [ { "key": "mobile", "clicks": 8000 }, { "key": "desktop", "clicks": 4345 } ],
  "browsers": [ { "key": "Chrome", "clicks": 7000 } ]
}
```
- Bot traffic is excluded. Analytics lag the live redirects by a few seconds.
- Country `ZZ` = unknown. **Locally almost every click is `ZZ`**, because client IPs are private Docker addresses (see §8.6 for the test override).
- Errors: `404 URL_NOT_FOUND`, `400 VALIDATION_ERROR`, `503 ANALYTICS_UNAVAILABLE` (ClickHouse down, or the `analytics` profile is not running).

**GET `/api/v1/urls/{code}/clicks?limit=50&cursor=…`** — recent raw clicks (last 90 days), newest first:
```json
{ "items": [ { "ts": "2026-11-22T10:15:31.004Z", "country": "IN", "referrerHost": "t.co", "device": "mobile", "browser": "Chrome" } ], "nextCursor": null }
```

### 3.8 Redirect — `GET /{code}`

| Case | Status | Headers / Body |
|---|---|---|
| Active | `302 Found` | `Location: <originalUrl>`, `Cache-Control: private, no-store`, `X-Robots-Tag: noindex` |
| Unknown code | `404` | Error body |
| Expired | `410` | `code: URL_EXPIRED` |
| Deleted | `410` | `code: URL_DELETED` |
| Disabled by admin | `410` | `code: URL_DISABLED` |

- **Content negotiation:** if `Accept` contains `text/html`, a minimal HTML error page is returned; otherwise `application/problem+json`.
- `HEAD` behaves like `GET` but **does not** emit a click event.
- Nginx only forwards paths matching `^/[A-Za-z0-9_-]{4,32}$`; anything else is rejected by Nginx with 404 without touching the app.

### 3.9 Admin

**PATCH `/api/v1/admin/urls/{code}`**
```json
{ "status": "DISABLED", "reason": "Phishing reported via abuse inbox" }
```
`status` ∈ `DISABLED | ACTIVE`. `reason` is required when disabling (≤ 500 chars). Returns `200` with the URL object plus `disabledAt`, `disabledReason`. Writes an `audit_log` row and invalidates caches (§6.7).

**GET `/api/v1/admin/urls?status=&userId=&domain=&limit=&cursor=`** — paginated search.

**POST `/api/v1/admin/blocked-domains`**
```json
{ "domain": "evil.example", "reason": "malware" }
```
Blocks the domain and all subdomains for **new** links. With `"disableExisting": true` it also disables existing active links whose host matches (runs as a background batch; the response returns `202` with a count).

**GET `/api/v1/admin/stats`**
```json
{ "totalUrls": 1830000, "urlsCreatedLast24h": 1000000, "activeUsersLast24h": 51234, "disabledUrls": 312, "clicksLast24h": 99876543 }
```

### 3.10 Health

| Path | Meaning | 200 when |
|---|---|---|
| `/health/live` | Process is running | Always (unless deadlocked) |
| `/health/ready` | Instance can serve traffic | PG primary **or** replica reachable, and not shutting down |

`/health/ready` body reports each dependency so degraded mode is visible:
```json
{ "status": "UP", "components": { "postgresPrimary": "UP", "postgresReplica": "DISABLED", "redis": "DOWN", "kafka": "UP", "clickhouse": "UP" } }
```
Redis, Kafka and ClickHouse being down does **not** make the instance unready. Those are degradations, not outages (HLD §11).

### 3.11 Error Body (RFC 9457)

```json
{
  "type": "https://docs.short.local/errors/alias-taken",
  "title": "Alias already in use",
  "status": 409,
  "code": "ALIAS_TAKEN",
  "detail": "The alias 'galaxy-ultra' is already taken.",
  "instance": "/api/v1/urls",
  "requestId": "4f1c2a9e8b7d6c5e",
  "errors": [ { "field": "customAlias", "message": "already taken" } ]
}
```
`errors[]` appears only for validation failures. Stack traces and internal messages are never returned.

---

## 4. Data Design

### 4.1 PostgreSQL Schema

Migrations are run by Flyway (one-shot `migrate` container, §9). Files live in `infra/postgres/migrations/`.

**V1__users.sql**
```sql
CREATE TABLE users (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          TEXT        NOT NULL,
    password_hash  TEXT        NOT NULL,              -- argon2id
    role           TEXT        NOT NULL DEFAULT 'USER'
                   CHECK (role IN ('USER', 'ADMIN')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_email_len CHECK (char_length(email) <= 254)
);
CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email));
```

**V2__urls.sql**
```sql
CREATE TABLE urls (
    id               BIGINT      PRIMARY KEY,          -- from block allocator (§6.1)
    short_code       VARCHAR(32) NOT NULL,
    original_url     TEXT        NOT NULL,
    user_id          BIGINT      NOT NULL REFERENCES users(id),
    is_custom        BOOLEAN     NOT NULL DEFAULT false,
    status           TEXT        NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE', 'DELETED', 'DISABLED')),
    expires_at       TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ NULL,
    disabled_at      TIMESTAMPTZ NULL,
    disabled_reason  TEXT        NULL,
    disabled_by      BIGINT      NULL REFERENCES users(id),

    CONSTRAINT uq_urls_short_code UNIQUE (short_code),
    CONSTRAINT chk_url_len   CHECK (char_length(original_url) <= 2048),
    CONSTRAINT chk_expiry    CHECK (expires_at IS NULL OR expires_at > created_at),
    CONSTRAINT chk_deleted   CHECK ((status = 'DELETED')  = (deleted_at  IS NOT NULL)),
    CONSTRAINT chk_disabled  CHECK ((status = 'DISABLED') = (disabled_at IS NOT NULL))
);

-- "List my URLs" with keyset pagination; deleted rows excluded from the index
CREATE INDEX idx_urls_user_created
    ON urls (user_id, created_at DESC, id DESC)
    WHERE status <> 'DELETED';

-- Admin search by status
CREATE INDEX idx_urls_status_created ON urls (status, created_at DESC) WHERE status <> 'ACTIVE';
```

`chk_disabled` means a DISABLED link that an admin re-enables gets `disabled_at = NULL` again; history lives in `audit_log`.

**V3__id_blocks_and_idempotency.sql**
```sql
CREATE TABLE id_blocks (
    name        TEXT   PRIMARY KEY,
    next_value  BIGINT NOT NULL CHECK (next_value >= 1)
);
INSERT INTO id_blocks (name, next_value) VALUES ('url', 1);

CREATE TABLE idempotency_keys (
    user_id       BIGINT      NOT NULL REFERENCES users(id),
    idem_key      VARCHAR(64) NOT NULL,
    request_hash  CHAR(64)    NOT NULL,          -- sha256 hex of canonical request body
    url_id        BIGINT      NULL REFERENCES urls(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, idem_key)
);
CREATE INDEX idx_idem_created ON idempotency_keys (created_at);   -- for the 24 h purge job

-- Boot-time guards, e.g. ('code_permutation_key_fp', '<sha256 of key>') — see §6.2
CREATE TABLE system_settings (
    name        TEXT        PRIMARY KEY,
    value       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**V4__abuse_and_audit.sql**
```sql
CREATE TABLE reserved_aliases (
    alias TEXT PRIMARY KEY                       -- stored lower-case
);

CREATE TABLE blocked_domains (
    domain      TEXT        PRIMARY KEY,          -- lower-case, punycode
    reason      TEXT        NOT NULL,
    created_by  BIGINT      NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id    BIGINT      NOT NULL REFERENCES users(id),
    action      TEXT        NOT NULL,             -- URL_DISABLED, URL_ENABLED, DOMAIN_BLOCKED, ...
    target      TEXT        NOT NULL,             -- short code or domain
    details     JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_target ON audit_log (target, created_at DESC);
```

**V5__seed.sql**
```sql
INSERT INTO reserved_aliases (alias) VALUES
  ('api'), ('admin'), ('health'), ('metrics'), ('login'), ('logout'), ('register'),
  ('static'), ('assets'), ('docs'), ('help'), ('about'), ('terms'), ('privacy'),
  ('support'), ('status'), ('dashboard'), ('account'), ('settings'), ('null'), ('undefined')
ON CONFLICT DO NOTHING;
```
The admin user is **not** seeded in SQL (it needs a password hash). `url-service` creates it on boot from `ADMIN_EMAIL` / `ADMIN_PASSWORD` if absent, guarded by a `pg_advisory_lock` so two replicas don't race.

**Connection budget:** Postgres `max_connections = 100`. Each url-service replica uses a write pool of 10 and a read pool of 20 → 2 replicas = 60 connections, 3 replicas = 90. Going beyond 3 replicas locally needs smaller pools or PgBouncer.

### 4.2 Redis Key Design

| Key | Type | Value | TTL | Written by |
|---|---|---|---|---|
| `url:{code}` | String (JSON) | `{"u":"<originalUrl>","s":"ACTIVE","e":1798675200000}` (`e` = expiresAt epoch ms or `null`) | `min(24h, expiresAt − now) ± 10% jitter` | create (write-through), redirect miss |
| `url:{code}` (negative) | String | `{"nf":1}` | 60 s | redirect miss on unknown code |
| `rl:create:{userId}` | Hash | `t` (tokens), `ts` (last refill ms) | `capacity / rate` | Lua token bucket (§6.8) |
| `rl:login:{ipHash}:{emailHash}` | Hash | same | same | login |
| `recentwrite:{userId}` | String | `1` | 5 s | create / delete (read-your-writes, §6.9) |
| Channel `cache-invalidate` | Pub/Sub | `{code}` | — | delete / disable / enable |

Deleted and disabled entries are cached with their status (`"s":"DELETED"`), so repeated hits on dead links don't reach the DB.

**Eviction note:** locally a single Redis runs `allkeys-lru`, so under memory pressure rate-limit buckets can be evicted too, which briefly resets a user's limit. That is acceptable locally. Production splits this into a cache Redis (LRU) and a counters Redis (`noeviction`).

### 4.3 Kafka Topics

| Topic | Partitions | Key | Retention (local) | Purpose |
|---|---:|---|---|---|
| `click-events` | 6 | `shortCode` | 24 h | Redirect click events |
| `click-events.dlq` | 1 | `shortCode` | 7 d | Messages the worker cannot parse |

**`click-events` value (JSON, schema v1)**
```json
{
  "v": 1,
  "eventId": "0193a6c4-5f2e-7b1a-9c3d-2e4f6a8b0c1d",
  "shortCode": "aB91xK7",
  "ts": "2026-11-22T10:15:31.004Z",
  "ip": "203.0.113.9",
  "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) ...",
  "referrer": "https://t.co/xyz",
  "requestId": "4f1c2a9e8b7d6c5e"
}
```
- `eventId` is a UUIDv7 (time-ordered).
- `ip` is the only personal field. It exists in Kafka only (short retention) and is dropped by the worker after geo lookup (NFR-05).
- Schema evolution: add optional fields only; bump `v` for breaking changes. The worker routes unknown `v` to the DLQ.

### 4.4 ClickHouse Schema

`infra/clickhouse/init/01-schema.sql` (runs on first container start):

```sql
CREATE DATABASE IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.click_events
(
    event_id       UUID,
    short_code     String,
    ts             DateTime64(3, 'UTC'),
    country        LowCardinality(String),   -- ISO-3166 alpha-2, 'ZZ' = unknown
    referrer_host  LowCardinality(String),   -- '(direct)' when absent
    device         LowCardinality(String),   -- mobile | desktop | tablet | other
    browser        LowCardinality(String),
    os             LowCardinality(String),
    is_bot         UInt8
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (short_code, ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS analytics.click_daily
(
    short_code     String,
    day            Date,
    country        LowCardinality(String),
    referrer_host  LowCardinality(String),
    device         LowCardinality(String),
    browser        LowCardinality(String),
    clicks         UInt64
)
ENGINE = SummingMergeTree(clicks)
PARTITION BY toYYYYMM(day)
ORDER BY (short_code, day, country, referrer_host, device, browser);

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.mv_click_daily
TO analytics.click_daily AS
SELECT short_code, toDate(ts) AS day, country, referrer_host, device, browser, count() AS clicks
FROM analytics.click_events
WHERE is_bot = 0
GROUP BY short_code, day, country, referrer_host, device, browser;
```

Queries always `sum(clicks)` with `GROUP BY`, because SummingMergeTree merges parts lazily:

```sql
-- timeseries
SELECT day, sum(clicks) AS clicks
FROM analytics.click_daily
WHERE short_code = {code:String} AND day BETWEEN {from:Date} AND {to:Date}
GROUP BY day ORDER BY day;

-- top countries
SELECT country AS key, sum(clicks) AS clicks
FROM analytics.click_daily
WHERE short_code = {code:String} AND day BETWEEN {from:Date} AND {to:Date}
GROUP BY key ORDER BY clicks DESC LIMIT 10;
```

---

## 5. Class Design

### 5.1 Domain Model

```mermaid
classDiagram
    class Url {
        +Long id
        +ShortCode code
        +String originalUrl
        +Long ownerId
        +boolean isCustom
        +UrlStatus status
        +Instant expiresAt
        +Instant createdAt
        +Instant deletedAt
        +Instant disabledAt
        +String disabledReason
        +effectiveStatus(now) EffectiveStatus
        +delete(now)
        +disable(adminId, reason, now)
        +enable()
        +isOwnedBy(userId) boolean
    }
    class ShortCode {
        <<value object>>
        +String value
        +isGenerated() boolean
    }
    class UrlStatus {
        <<enum>>
        ACTIVE
        DELETED
        DISABLED
    }
    class EffectiveStatus {
        <<enum>>
        ACTIVE
        EXPIRED
        DELETED
        DISABLED
    }
    class User {
        +Long id
        +String email
        +String passwordHash
        +Role role
    }
    class Role {
        <<enum>>
        USER
        ADMIN
    }
    class CacheEntry {
        <<value object>>
        +String originalUrl
        +UrlStatus status
        +Instant expiresAt
        +boolean notFound
        +fromUrl(Url) CacheEntry$
        +notFound() CacheEntry$
    }
    class ResolveResult {
        <<sealed>>
    }
    class Redirect {
        +String location
    }
    class NotFound
    class Gone {
        +EffectiveStatus reason
    }
    class ClickEvent {
        +UUID eventId
        +String shortCode
        +Instant ts
        +String ip
        +String userAgent
        +String referrer
        +String requestId
    }

    Url --> ShortCode
    Url --> UrlStatus
    Url ..> EffectiveStatus
    User --> Role
    ResolveResult <|-- Redirect
    ResolveResult <|-- NotFound
    ResolveResult <|-- Gone
    CacheEntry ..> Url
```

### 5.2 url-service — Application and Infrastructure

```mermaid
classDiagram
    direction LR

    class RedirectController {
        +get(code, request) HttpResponse
        +head(code, request) HttpResponse
    }
    class UrlController {
        +create(req, idemKey, principal)
        +list(query, principal)
        +get(code, principal)
        +delete(code, principal)
    }
    class AnalyticsController
    class AdminController
    class AuthController

    class RedirectService {
        +resolve(code, now) ResolveResult
    }
    class UrlService {
        +create(cmd, principal, idemKey) UrlView
        +list(userId, filter, page) Page~UrlView~
        +get(code, userId) UrlView
        +delete(code, userId)
    }
    class AdminService {
        +setStatus(code, status, reason, adminId)
        +blockDomain(domain, reason, adminId, disableExisting)
    }
    class AnalyticsQueryService {
        +summary(code, userId, range) AnalyticsView
        +recentClicks(code, userId, page) Page~ClickView~
    }
    class AuthService {
        +register(email, password) User
        +login(email, password, ip) Token
    }

    class ShortCodeGenerator {
        +next() ShortCode
    }
    class IdAllocator {
        <<interface>>
        +nextId() Long
    }
    class BlockIdAllocator
    class CodePermutation {
        <<interface>>
        +permute(Long) Long
        +invert(Long) Long
    }
    class FeistelPermutation
    class Base62Codec {
        +encode(Long, width) String
        +decode(String) Long
    }

    class UrlValidator {
        +validate(raw) NormalizedUrl
    }
    class AliasValidator {
        +validate(alias)
    }
    class DomainBlocklist {
        +isBlocked(host) boolean
        +refresh()
    }

    class UrlCache {
        <<interface>>
        +get(code) Optional~CacheEntry~
        +put(code, entry)
        +evict(code)
    }
    class TieredUrlCache
    class LocalLruCache
    class RedisUrlCache
    class CacheInvalidationListener
    class SingleFlight~K,V~ {
        +execute(key, supplier) V
    }
    class CircuitBreaker {
        +call(supplier, fallback)
        +state() State
    }

    class RateLimiter {
        <<interface>>
        +tryAcquire(key, policy) RateDecision
    }
    class RedisTokenBucketLimiter
    class LocalTokenBucketLimiter
    class FallbackRateLimiter

    class UrlRepository {
        <<interface>>
        +insert(Url)
        +findByCode(code, ReadPreference) Optional~Url~
        +findByOwner(userId, filter, cursor, limit) List~Url~
        +update(Url)
    }
    class IdempotencyRepository {
        <<interface>>
        +tryInsert(userId, key, hash) boolean
        +find(userId, key) Optional~IdemRecord~
        +attachUrl(userId, key, urlId)
    }
    class IdBlockRepository {
        <<interface>>
        +reserve(name, size) Range
    }
    class TransactionManager {
        +inTransaction(fn)
    }

    class ClickEventPublisher {
        <<interface>>
        +publish(ClickEvent)
    }
    class KafkaClickEventPublisher
    class NoopClickEventPublisher
    class ClickHouseAnalyticsReader

    RedirectController --> RedirectService
    RedirectController --> ClickEventPublisher
    UrlController --> UrlService
    AnalyticsController --> AnalyticsQueryService
    AdminController --> AdminService
    AuthController --> AuthService

    RedirectService --> UrlCache
    RedirectService --> UrlRepository
    RedirectService --> SingleFlight

    UrlService --> ShortCodeGenerator
    UrlService --> UrlValidator
    UrlService --> AliasValidator
    UrlService --> UrlRepository
    UrlService --> IdempotencyRepository
    UrlService --> TransactionManager
    UrlService --> UrlCache
    UrlService --> RateLimiter

    AdminService --> UrlRepository
    AdminService --> UrlCache
    AdminService --> DomainBlocklist
    AnalyticsQueryService --> UrlRepository
    AnalyticsQueryService --> ClickHouseAnalyticsReader

    ShortCodeGenerator --> IdAllocator
    ShortCodeGenerator --> CodePermutation
    ShortCodeGenerator --> Base62Codec
    IdAllocator <|.. BlockIdAllocator
    BlockIdAllocator --> IdBlockRepository
    CodePermutation <|.. FeistelPermutation
    UrlValidator --> DomainBlocklist

    UrlCache <|.. TieredUrlCache
    TieredUrlCache --> LocalLruCache : L1
    TieredUrlCache --> RedisUrlCache : L2
    RedisUrlCache --> CircuitBreaker
    CacheInvalidationListener --> LocalLruCache

    RateLimiter <|.. FallbackRateLimiter
    FallbackRateLimiter --> RedisTokenBucketLimiter
    FallbackRateLimiter --> LocalTokenBucketLimiter
    FallbackRateLimiter --> CircuitBreaker

    ClickEventPublisher <|.. KafkaClickEventPublisher
    ClickEventPublisher <|.. NoopClickEventPublisher
```

- `NoopClickEventPublisher` is wired when `ANALYTICS_ENABLED=false` (the `analytics` profile is off).
- `RedisUrlCache` returns `Optional.empty()` when the breaker is open, so callers treat Redis downtime as a cache miss.
- `UrlRepository.findByCode` takes a `ReadPreference` (`REPLICA`, `PRIMARY`, `REPLICA_THEN_PRIMARY`) (§6.9).

### 5.3 analytics-worker

```mermaid
classDiagram
    direction LR
    class ClickEventConsumer {
        +run()
        -pollLoop()
        -commit(offsets)
    }
    class EventParser {
        +parse(bytes) Result~ClickEvent~
    }
    class Enricher {
        +enrich(ClickEvent) EnrichedClick
    }
    class GeoIpResolver {
        <<interface>>
        +country(ip) String
    }
    class MaxMindGeoIpResolver
    class NullGeoIpResolver
    class UserAgentParser {
        +parse(ua) DeviceInfo
    }
    class ReferrerNormalizer {
        +host(referrer) String
    }
    class BatchBuffer {
        +add(EnrichedClick)
        +shouldFlush(now) boolean
        +drain() List~EnrichedClick~
    }
    class ClickHouseWriter {
        +insert(List~EnrichedClick~)
    }
    class DlqPublisher {
        +send(record, error)
    }
    class EnrichedClick {
        +UUID eventId
        +String shortCode
        +Instant ts
        +String country
        +String referrerHost
        +String device
        +String browser
        +String os
        +boolean isBot
    }

    ClickEventConsumer --> EventParser
    ClickEventConsumer --> Enricher
    ClickEventConsumer --> BatchBuffer
    ClickEventConsumer --> ClickHouseWriter
    ClickEventConsumer --> DlqPublisher
    Enricher --> GeoIpResolver
    Enricher --> UserAgentParser
    Enricher --> ReferrerNormalizer
    GeoIpResolver <|.. MaxMindGeoIpResolver
    GeoIpResolver <|.. NullGeoIpResolver
    Enricher ..> EnrichedClick
```

`NullGeoIpResolver` (always `ZZ`) is used when no GeoLite2 `.mmdb` file is mounted, which is the default locally.

### 5.4 URL Lifecycle

```mermaid
stateDiagram-v2
    [*] --> ACTIVE : create
    ACTIVE --> EXPIRED : now ≥ expiresAt (computed, not stored)
    ACTIVE --> DELETED : owner delete
    ACTIVE --> DISABLED : admin disable
    DISABLED --> ACTIVE : admin enable
    DISABLED --> DELETED : owner delete
    EXPIRED --> DELETED : owner delete
    DELETED --> [*]
    note right of DELETED
        Terminal. Row is kept so the code
        returns 410 and is never reused.
    end note
```

---

## 6. Algorithms

### 6.1 Block ID Allocator

```text
const BLOCK_SIZE = env.ID_BLOCK_SIZE            // 1000
const MAX_ID     = 62^7 - 1                      // 3_521_614_606_207
const PREFETCH_AT = 0.8                          // refill when 80% of block used

state: current = Range(empty), next = Optional<Range>, lock

function nextId():
    lock.acquire()
    try:
        if current.isExhausted():
            current = next.takeOrElse(() -> reserveBlockWithRetry())   // sync only on cold path
        id = current.take()
        if current.usedFraction() >= PREFETCH_AT and next.isEmpty() and !prefetchInFlight:
            prefetchInFlight = true
            async { next = reserveBlockWithRetry(); prefetchInFlight = false }
        return id
    finally:
        lock.release()

function reserveBlockWithRetry():
    retry up to 3 times, backoff 50ms, 100ms, 200ms:
        start = SQL:
            UPDATE id_blocks
               SET next_value = next_value + :BLOCK_SIZE
             WHERE name = 'url'
         RETURNING next_value - :BLOCK_SIZE;
        if start + BLOCK_SIZE - 1 > MAX_ID: raise IdSpaceExhausted      // alert at 80% of MAX_ID
        return Range(start, start + BLOCK_SIZE - 1)
    raise ServiceUnavailable("ID allocation failed")
```

- **Correctness:** the `UPDATE … RETURNING` is atomic under a row lock, so two replicas can never receive overlapping ranges.
- **Retry safety:** a retry after an unknown outcome at worst wastes one block (gaps are harmless).
- **Crash:** unused IDs in a dead replica's block are lost, also harmless.
- **Cost:** 1 DB round trip per 1,000 URLs; prefetch keeps it off the request path.

### 6.2 Code Permutation (Feistel with Cycle-Walking)

Goal: a keyed bijection `P: [0, N) → [0, N)` with `N = 62⁷`, so sequential IDs produce scattered codes.

`N = 3,521,614,606,208 < 2⁴² = 4,398,046,511,104`, so we use a balanced Feistel network on 42 bits (two 21-bit halves) and cycle-walk values that fall outside `[0, N)`.

```text
const N         = 62^7
const HALF      = 21
const MASK      = (1 << 21) - 1
const ROUNDS    = 4
roundKeys[i]    = first 4 bytes of HMAC-SHA256(CODE_PERMUTATION_KEY, "feistel-round-" + i)   // uint32

function F(r, k):                         // round function, uint32 arithmetic
    h = (r XOR k)
    h = h XOR (h >>> 16); h = h * 0x85EBCA6B
    h = h XOR (h >>> 13); h = h * 0xC2B2AE35
    h = h XOR (h >>> 16)
    return h AND MASK

function feistel42(x):
    L = x >>> HALF; R = x AND MASK
    for i in 0 until ROUNDS:
        (L, R) = (R, L XOR F(R, roundKeys[i]))
    return (L << HALF) OR R

function permute(id):                     // 0 <= id < N
    y = feistel42(id)
    while y >= N:                         // cycle-walk; expected ~1.25 iterations
        y = feistel42(y)
    return y

function invert(code):                    // for debugging/tests only
    // same structure with keys in reverse order and halves swapped
```

- **Bijectivity:** a Feistel network is a permutation on `[0, 2⁴²)`. Cycle-walking restricted to `[0, N)` is a permutation on `[0, N)`. Therefore distinct IDs → distinct codes, with no DB check needed.
- **Not cryptography.** This is obfuscation against casual enumeration, not a security boundary.
- ⚠️ **`CODE_PERMUTATION_KEY` must never change** after the first URL is created. A new key would map future IDs onto codes that may already exist. The service refuses to start if the key's SHA-256 fingerprint differs from the one stored in a `system_settings` row written on first boot.

### 6.3 Base62 Encoding

```text
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

function encode(n, width = 7):
    chars = []
    repeat width times:
        chars.prepend(ALPHABET[n mod 62]); n = n div 62
    assert n == 0
    return join(chars)                    // fixed width, left-padded with '0'

function generateCode():
    return encode(permute(idAllocator.nextId()), 7)
```

### 6.4 URL Validation and Normalisation

```text
function validate(raw):
    s = trim(raw)
    if s is empty or length(s) > 2048:            fail INVALID_URL
    uri = parseStrictRfc3986(s)                    else fail INVALID_URL
    scheme = lower(uri.scheme)
    if scheme not in {"http", "https"}:            fail URL_SCHEME_NOT_ALLOWED
    if uri.host is empty:                          fail INVALID_URL
    if uri.userInfo is present:                    fail INVALID_URL     // "https://bank.com@evil.com"
    host = toAsciiPunycode(lower(uri.host))
    if host == BASE_URL.host or host in SHORTENER_DOMAINS:
                                                   fail INVALID_URL     // redirect loop
    if isIpLiteral(host) and isPrivateLoopbackOrLinkLocal(host) and !ALLOW_PRIVATE_TARGETS:
                                                   fail INVALID_URL
    if host == "localhost" and !ALLOW_PRIVATE_TARGETS:
                                                   fail INVALID_URL
    if blocklist.isBlocked(host):                  fail URL_BLOCKED (403)
    port = uri.port == defaultPort(scheme) ? none : uri.port
    return rebuild(scheme, host, port, uri.rawPath, uri.rawQuery, uri.rawFragment)
           // path/query/fragment kept byte-for-byte; only scheme/host/port normalised
```

`DomainBlocklist.isBlocked(host)` checks `host` and each parent (`a.b.evil.example` → `b.evil.example` → `evil.example`) against an in-memory set loaded from `blocked_domains`. It refreshes every 60 s and immediately on the `cache-invalidate` message `"__blocklist__"`.

The service never fetches the target URL, so there is no SSRF surface. Private-IP rejection is purely abuse prevention.

### 6.5 Custom Alias Validation

```text
function validateAlias(a):
    if not matches(a, ^[A-Za-z0-9_-]{4,32}$):           fail ALIAS_INVALID
    if matches(a, ^[A-Za-z0-9]{4,7}$):                    fail ALIAS_INVALID
        // pure-alphanumeric aliases must be ≥ 8 chars or contain '-'/'_'
        // → can never collide with the 7-char generated namespace
    if lower(a) in reservedAliases:                      fail ALIAS_RESERVED (409)
    // uniqueness is enforced by uq_urls_short_code at insert time → ALIAS_TAKEN (409)
```

Aliases are case-sensitive like generated codes. The reserved-word check is case-insensitive.

### 6.6 Create URL (with Idempotency)

```text
function create(cmd, principal, idemKey):
    rateLimiter.tryAcquire("rl:create:" + principal.id, CREATE_POLICY)  else fail RATE_LIMITED
    url = urlValidator.validate(cmd.originalUrl)
    if cmd.customAlias: aliasValidator.validate(cmd.customAlias)
    validateExpiry(cmd.expiresAt)

    id   = idAllocator.nextId()                       // outside the transaction
    code = cmd.customAlias ?: generateCode(id)
    hash = sha256(canonicalJson(cmd))

    result = tx.inTransaction(() ->
        if idemKey:
            inserted = idempotencyRepo.tryInsert(principal.id, idemKey, hash)
                // INSERT ... ON CONFLICT (user_id, idem_key) DO NOTHING
                // If a concurrent tx holds the same key, Postgres blocks here until it commits/aborts.
            if not inserted:
                rec = idempotencyRepo.find(principal.id, idemKey)
                if rec.requestHash != hash:  fail IDEMPOTENCY_KEY_REUSED (422)
                return Replay(urlRepo.findById(rec.urlId))          // same 201 body
        urlRepo.insert(Url(id, code, url, principal.id, isCustom = cmd.customAlias != null, ...))
            // unique violation on short_code → rollback → ALIAS_TAKEN (409)
            // (cannot happen for generated codes, by §6.2)
        if idemKey: idempotencyRepo.attachUrl(principal.id, idemKey, id)
        return Created(url)
    )

    afterCommit:
        cache.put(code, CacheEntry.fromUrl(result.url))   // best effort; errors swallowed + metered
        redis.SET recentwrite:{userId} 1 EX 5             // best effort
    return result
```

- Failed creates (e.g. `ALIAS_TAKEN`) roll back the idempotency row too, so a retry gets the same deterministic error again rather than a stale replay.
- A purge job deletes `idempotency_keys` rows older than 24 h (§6.12).

### 6.7 Redirect Resolution

```text
function resolve(code, now):
    entry = l1.get(code)
    if entry is empty:
        entry = singleFlight.execute(code, () -> loadThroughL2(code))
        l1.put(code, entry, ttl = L1_TTL)                          // 30 s
    return decide(entry, now)

function loadThroughL2(code):
    cached = redisCache.get(code)                                  // empty on miss OR breaker open
    if cached.present: return cached.value
    url = urlRepo.findByCode(code, REPLICA_THEN_PRIMARY_ON_MISS)   // §6.9
    entry = url.present ? CacheEntry.fromUrl(url) : CacheEntry.notFound()
    redisCache.put(code, entry, ttl = entry.notFound ? 60s : l2Ttl(entry))   // best effort
    return entry

function decide(e, now):
    if e.notFound:                          return NotFound
    if e.status == DELETED:                 return Gone(DELETED)
    if e.status == DISABLED:                return Gone(DISABLED)
    if e.expiresAt != null and now >= e.expiresAt:
                                            return Gone(EXPIRED)
    return Redirect(e.originalUrl)

function l2Ttl(e):
    base = 24h
    if e.expiresAt != null: base = min(base, e.expiresAt - now + 1h)   // keep a while to answer 410 cheaply
    return base * random(0.9, 1.1)
```

Controller, after `resolve`:
```text
if result is Redirect and method == GET:
    clickPublisher.publish(ClickEvent(uuidv7(), code, now, clientIp(req), req.ua, req.referer, req.requestId))
        // non-blocking enqueue; never throws
respond(result)
```

**Invalidation** (delete / disable / enable), after the DB commit:
```text
redis.DEL url:{code}
redis.PUBLISH cache-invalidate {code}
l1.evict(code)                 // local instance immediately; others via subscription
```
Every replica's `CacheInvalidationListener` evicts the code from L1 when the message arrives. If Redis is down, invalidation is lost and the 30 s L1 TTL bounds staleness. The Redis entry is already gone with Redis.

### 6.8 Token-Bucket Rate Limiter (Redis Lua)

```lua
-- KEYS[1] = bucket key
-- ARGV[1] = capacity (tokens), ARGV[2] = refill rate (tokens per ms), ARGV[3] = cost
local cap   = tonumber(ARGV[1])
local rate  = tonumber(ARGV[2])
local cost  = tonumber(ARGV[3])
local t     = redis.call('TIME')                      -- Redis clock: no skew between replicas
local now   = t[1] * 1000 + math.floor(t[2] / 1000)

local b      = redis.call('HMGET', KEYS[1], 't', 'ts')
local tokens = tonumber(b[1]) or cap
local ts     = tonumber(b[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)

local allowed = tokens >= cost
if allowed then tokens = tokens - cost end

redis.call('HSET', KEYS[1], 't', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate))

local retryMs = 0
if not allowed then retryMs = math.ceil((cost - tokens) / rate) end
return { allowed and 1 or 0, math.floor(tokens), retryMs }
```

| Policy | Capacity | Refill | Key |
|---|---:|---|---|
| Create URL | 20 | 100 / hour | `rl:create:{userId}` |
| Login | 5 | 5 / minute | `rl:login:{sha1(ip)}:{sha1(email)}` |
| Admin | exempt | — | — |
| Redirect / per-IP flood | Nginx `limit_req` (50 r/s, burst 200) | — | `$binary_remote_addr` |

`FallbackRateLimiter`: when the Redis breaker is open, it uses an in-memory token bucket per instance with capacity divided by the expected replica count (`RATE_LIMIT_LOCAL_DIVISOR`, default 2). This is less precise but still protective.

### 6.9 Read Routing and Read-Your-Writes

| Operation | Read preference |
|---|---|
| Redirect cache miss | `REPLICA`; **if not found → retry once on `PRIMARY`** before returning 404 |
| List / get my URLs | `PRIMARY` if `recentwrite:{userId}` exists (≤ 5 s since last write), else `REPLICA` |
| Admin, auth, ownership checks before mutation | `PRIMARY` |
| Any replica error / replica not configured | Fall back to `PRIMARY` |

The redirect retry closes a real race: a code created a moment ago, evicted from Redis, and not yet replicated would otherwise return 404 **and get negatively cached for 60 s**. The extra primary query only happens for codes that are genuinely unknown, and those are then negatively cached, so primary load stays bounded.

### 6.10 Single-Flight and Circuit Breaker

**Single-flight (per instance):** a concurrent map `code → in-flight future`. The first caller loads; concurrent callers await the same future; the entry is removed when it completes. This stops a burst of misses on one hot code from turning into N DB queries.

**Circuit breaker** (one per dependency: `redis`, `pg-replica`, `clickhouse`):

```mermaid
stateDiagram-v2
    [*] --> CLOSED
    CLOSED --> OPEN : ≥ 50% failures over last 20 calls (min 10 calls)<br/>or ≥ 5 consecutive timeouts
    OPEN --> HALF_OPEN : after 10 s
    HALF_OPEN --> CLOSED : 3 trial calls succeed
    HALF_OPEN --> OPEN : any trial call fails
```

While OPEN, calls return the fallback immediately without waiting for a timeout. State is exported as `circuit_breaker_state{dependency}` (0 = closed, 1 = half-open, 2 = open).

### 6.11 Click Event Publishing (url-service)

```text
queue = BoundedQueue<ClickEvent>(capacity = CLICK_QUEUE_CAPACITY)     // 10_000

publish(event):                          // request thread
    if not queue.offer(event):           // non-blocking
        metrics.analytics_events_dropped_total.inc()

producerLoop():                          // 1 background thread
    loop:
        batch = queue.drain(max = 500, waitUpTo = 50ms)
        for e in batch: kafka.sendAsync(topic = "click-events", key = e.shortCode, value = json(e))
        // client config: acks=1, linger.ms=20, compression=lz4,
        //                retries=3, delivery.timeout.ms=30000, max.block.ms=0
        // send callback failure → analytics_events_failed_total.inc()

onShutdown():
    stop accepting; drain queue to Kafka; flush with a 5 s deadline; then exit
```

`max.block.ms=0` guarantees the producer never blocks a thread when Kafka metadata is unavailable.

### 6.12 Analytics Worker Loop

```text
consumer group "analytics-worker", enable.auto.commit = false, max.poll.records = 1000

loop:
    records = consumer.poll(200ms)
    for r in records:
        parsed = parser.parse(r.value)
        if parsed.failed: dlq.send(r, parsed.error); continue
        buffer.add(enricher.enrich(parsed.value))      // IP → country, UA → device/browser/os/is_bot,
                                                       // referrer → host; raw IP discarded here
    if buffer.size >= 5000 or buffer.age >= 1s:
        retry with backoff 1s, 2s, 4s … max 30s, forever:
            clickHouse.insert("analytics.click_events", buffer.rows, format = JSONEachRow)
            // while retrying: consumer.pause(all partitions), keep calling poll() to stay in the group
        consumer.commitSync(offsets of buffered records)
        consumer.resume(all); buffer.clear()
```

- **Semantics:** at-least-once. A crash between insert and commit replays a batch, giving small over-counts, which is accepted (HLD §6.3).
- **Ordering:** irrelevant for counting, so no per-partition ordering guarantees are needed beyond Kafka defaults.

### 6.13 Scheduled Jobs (inside url-service)

Each job runs on every replica's scheduler but takes a Postgres **advisory lock** (`pg_try_advisory_lock(<job-id>)`), so only one replica executes it per tick.

| Job | Schedule | Action |
|---|---|---|
| `purge-idempotency-keys` | every 10 min | `DELETE FROM idempotency_keys WHERE created_at < now() - interval '24 hours'` (batches of 5,000) |
| `id-space-watch` | every 1 h | Alert metric if `id_blocks.next_value > 0.8 × 62⁷` |
| `blocklist-disable-existing` | on demand (admin) | Disable active links of a newly blocked domain in batches of 1,000 + invalidate caches |

Expired links need **no job**: expiry is evaluated at read time.

---

## 7. Error Handling

### 7.1 Error Catalogue

| Code | HTTP | When |
|---|---:|---|
| `VALIDATION_ERROR` | 400 | Malformed JSON, missing/invalid fields (with `errors[]`) |
| `INVALID_URL` | 400 | Unparseable, too long, userinfo, loop, private target |
| `URL_SCHEME_NOT_ALLOWED` | 400 | Scheme other than http/https |
| `ALIAS_INVALID` | 400 | Alias fails charset/length/namespace rule |
| `EXPIRY_INVALID` | 400 | `expiresAt` in the past or > 5 years |
| `UNAUTHENTICATED` | 401 | Missing/invalid token |
| `TOKEN_EXPIRED` | 401 | Expired token |
| `INVALID_CREDENTIALS` | 401 | Login failed |
| `FORBIDDEN` | 403 | Non-admin on admin endpoint |
| `URL_BLOCKED` | 403 | Target domain blocklisted |
| `URL_NOT_FOUND` | 404 | Unknown code, or not owned by caller (API) |
| `ALIAS_TAKEN` | 409 | Unique violation on `short_code` |
| `ALIAS_RESERVED` | 409 | Alias in `reserved_aliases` |
| `EMAIL_TAKEN` | 409 | Registration with an existing email |
| `URL_EXPIRED` | 410 | Redirect on an expired link |
| `URL_DELETED` | 410 | Redirect on a deleted link |
| `URL_DISABLED` | 410 | Redirect on an admin-disabled link |
| `IDEMPOTENCY_KEY_REUSED` | 422 | Same key, different request body |
| `RATE_LIMITED` | 429 | Bucket empty (with `Retry-After`) |
| `INTERNAL_ERROR` | 500 | Unexpected exception |
| `SERVICE_UNAVAILABLE` | 503 | PG primary down on write path, ID allocation failed |
| `ANALYTICS_UNAVAILABLE` | 503 | ClickHouse down or analytics disabled |

### 7.2 Exception Hierarchy and Mapping

```mermaid
classDiagram
    class AppException {
        <<abstract>>
        +ErrorCode code
        +int httpStatus
        +String detail
        +List~FieldError~ fieldErrors
    }
    class ValidationException
    class AuthenticationException
    class AuthorizationException
    class NotFoundException
    class ConflictException
    class GoneException
    class RateLimitedException {
        +Duration retryAfter
    }
    class IdempotencyMismatchException
    class DependencyUnavailableException {
        +String dependency
    }

    AppException <|-- ValidationException
    AppException <|-- AuthenticationException
    AppException <|-- AuthorizationException
    AppException <|-- NotFoundException
    AppException <|-- ConflictException
    AppException <|-- GoneException
    AppException <|-- RateLimitedException
    AppException <|-- IdempotencyMismatchException
    AppException <|-- DependencyUnavailableException

    class GlobalErrorHandler {
        +handle(AppException) ProblemResponse
        +handle(UniqueViolation) ProblemResponse
        +handle(Throwable) ProblemResponse
    }
    GlobalErrorHandler ..> AppException
```

`GlobalErrorHandler` rules:
1. `AppException` → its code/status. Logged at `INFO` for 4xx, `WARN` for 503.
2. DB unique violation on `uq_urls_short_code` → `ALIAS_TAKEN`; on `uq_users_email_lower` → `EMAIL_TAKEN`.
3. DB connection/timeout errors on write paths → `SERVICE_UNAVAILABLE`, with `Retry-After: 2`.
4. Anything else → `INTERNAL_ERROR`, logged at `ERROR` with the stack trace. The response contains only `requestId`.

### 7.3 Timeouts, Retries and Fallbacks

| Call | Timeout (local) | Retries | Breaker | On failure |
|---|---|---|---|---|
| Redis GET (redirect) | 20 ms | 0 | `redis` | Treat as miss → DB |
| Redis SET / DEL / PUBLISH | 20 ms | 0 | `redis` | Ignore, increment `cache_errors_total` |
| Redis rate-limit script | 20 ms | 0 | `redis` | Local token bucket |
| PG replica read | 200 ms (statement_timeout) | 0 | `pg-replica` | Same query on primary |
| PG primary read | 500 ms | 0 | — | 503 |
| PG primary write | 1 s | 1, **only** if the connection could not be acquired (statement never sent) | — | 503 |
| ID block reserve | 500 ms | 3 (50/100/200 ms) | — | 503 |
| Kafka produce | async, `delivery.timeout.ms=30s` | client-internal 3 | — | Drop + metric |
| ClickHouse query (API) | 2 s | 0 | `clickhouse` | 503 `ANALYTICS_UNAVAILABLE` |
| ClickHouse insert (worker) | 10 s | ∞ with backoff ≤ 30 s | — | Pause consumption, don't commit |

Production tightens the Redis timeout to ~5 ms. Locally Docker networking jitter makes 20 ms the safer default. All values are environment-configurable (§9.4).

### 7.4 Logging Rules

- JSON to stdout: `ts, level, service, instance, requestId, method, route, status, latencyMs, userId, shortCode, msg`.
- **Never logged:** passwords, tokens, full original URLs (log `host` only, since query strings often contain secrets), raw client IPs (log a salted hash).
- The redirect path logs at `DEBUG` only. The Nginx access log already records each request, and logging 10k RPS at `INFO` would dominate CPU.

### 7.5 Graceful Shutdown

On `SIGTERM` (`docker compose stop`, 15 s grace):
1. `/health/ready` → 503; stop scheduler.
2. Keep serving in-flight requests for up to 10 s (Nginx's `max_fails` stops routing to the instance after failed attempts; `proxy_next_upstream` retries idempotent GETs on another replica).
3. Flush the click queue to Kafka (5 s deadline).
4. Close pools; exit 0.

---

## 8. Cross-Cutting Details

### 8.1 Authentication Middleware
Parse the Bearer token → verify signature and `exp` (60 s clock-skew leeway) → `Principal(userId, role)`. Admin routes require `role == ADMIN` → else 403 `FORBIDDEN`. Passwords are hashed with argon2id (m = 64 MB, t = 3, p = 1).

### 8.2 Ownership Checks
User-scoped endpoints load by `short_code` from the **primary** and compare `user_id`. A mismatch is reported as `URL_NOT_FOUND` (§1.1 #3).

### 8.3 Client IP
The app reads `X-Real-IP` only when the TCP peer is in `TRUSTED_PROXY_CIDRS` (the Compose network). Otherwise it uses the peer address.

### 8.4 Request ID
Nginx's `$request_id` → `X-Request-Id` → MDC/log context → Kafka event → error body. A single ID follows a click end to end.

### 8.5 Metrics Names
As listed in HLD §13, plus: `cache_errors_total{level}`, `analytics_events_failed_total`, `id_block_reservations_total`, `idempotent_replays_total`, `rate_limited_total{policy}`, `worker_batch_insert_seconds`, `worker_dlq_total`.

### 8.6 Local Geo Testing
When `APP_ENV=local` **and** `ALLOW_TEST_CLIENT_IP=true`, the url-service honours an `X-Test-Client-IP` header and puts that IP in the click event. Nginx passes the header through. This lets k6 simulate traffic from many countries when a GeoLite2 database is mounted at `./infra/geoip/GeoLite2-Country.mmdb`. Both flags default to off outside `local`.

---

## 9. Local Docker Compose Environment

### 9.1 Topology and Startup Order

```mermaid
flowchart TB
    pgp[postgres-primary] -->|healthy| migrate[migrate - flyway, one-shot]
    pgp -->|healthy| pgr[postgres-replica - profile ha]
    migrate -->|completed| svc[url-service × 2]
    redis[redis - profile cache] -.->|healthy, optional| svc
    pgr -.->|healthy, optional| svc
    kafka[kafka - profile analytics] -->|healthy| kinit[kafka-init - one-shot]
    kinit -.->|completed, optional| svc
    ch[clickhouse - profile analytics] -.->|healthy, optional| svc
    kinit -->|completed| worker[analytics-worker]
    ch -->|healthy| worker
    svc -->|healthy| nginx[nginx :8080]
    prom[prometheus - profile obs] --> graf[grafana - profile obs]
```

Dashed edges use `depends_on … required: false`, so url-service starts whether or not that profile is enabled. Missing dependencies are handled at runtime as degraded mode (empty `REDIS_URL` → cache disabled, `ANALYTICS_ENABLED=false` → no-op publisher).

### 9.2 Profiles

| Command | Running services |
|---|---|
| `docker compose up -d` | nginx, postgres-primary, migrate, url-service ×2 |
| `… --profile cache up -d` | + redis |
| `… --profile cache --profile analytics up -d` | + kafka, kafka-init, clickhouse, analytics-worker |
| `… --profile ha up -d` | + postgres-replica |
| `… --profile obs up -d` | + prometheus, grafana, redis-exporter, postgres-exporter |
| `COMPOSE_PROFILES=cache,analytics,ha,obs docker compose up -d` | Everything |

Set `COMPOSE_PROFILES` in `.env` to keep a default. The `.env` flags must match the profiles: `ANALYTICS_ENABLED=true` requires `analytics`, a non-empty `REDIS_URL` requires `cache`, and `DB_REPLICA_URL` requires `ha`.

### 9.3 `docker-compose.yml`

```yaml
name: url-shortener

x-app-env: &app-env
  APP_ENV: ${APP_ENV:-local}
  LOG_LEVEL: ${LOG_LEVEL:-info}

services:
  # ---------- core ----------
  nginx:
    image: nginx:1.27.3-alpine
    ports: ["8080:80"]
    environment:
      NGINX_RATE_LIMIT: ${NGINX_RATE_LIMIT:-50r/s}
    volumes:
      - ./infra/nginx/templates:/etc/nginx/templates:ro
    depends_on:
      url-service: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1/nginx-health"]
      interval: 10s
      timeout: 2s
      retries: 3

  postgres-primary:
    image: postgres:16.4
    environment:
      POSTGRES_DB: urlshortener
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      REPLICATION_PASSWORD: ${REPLICATION_PASSWORD}
    command: >
      postgres
      -c wal_level=replica
      -c max_wal_senders=5
      -c max_replication_slots=5
      -c max_connections=100
      -c shared_buffers=256MB
    ports: ["5432:5432"]
    volumes:
      - pg-primary-data:/var/lib/postgresql/data
      - ./infra/postgres/primary:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d urlshortener"]
      interval: 5s
      timeout: 3s
      retries: 10

  migrate:
    image: flyway/flyway:10.17.3
    command: >
      -url=jdbc:postgresql://postgres-primary:5432/urlshortener
      -user=app -password=${POSTGRES_PASSWORD}
      -locations=filesystem:/flyway/sql -connectRetries=10 migrate
    volumes:
      - ./infra/postgres/migrations:/flyway/sql:ro
    depends_on:
      postgres-primary: { condition: service_healthy }
    restart: "no"

  url-service:
    build: ./services/url-service
    deploy:
      replicas: ${URL_SERVICE_REPLICAS:-2}
    environment:
      <<: *app-env
      APP_PORT: 8000
      BASE_URL: http://localhost:8080
      DB_PRIMARY_URL: postgresql://app:${POSTGRES_PASSWORD}@postgres-primary:5432/urlshortener
      DB_REPLICA_URL: ${DB_REPLICA_URL:-}
      DB_POOL_SIZE_WRITE: 10
      DB_POOL_SIZE_READ: 20
      REDIS_URL: ${REDIS_URL:-}
      REDIS_TIMEOUT_MS: 20
      L1_CACHE_MAX_ENTRIES: 10000
      L1_CACHE_TTL_SECONDS: 30
      L2_CACHE_TTL_SECONDS: 86400
      NEGATIVE_CACHE_TTL_SECONDS: 60
      ID_BLOCK_SIZE: 1000
      CODE_PERMUTATION_KEY: ${CODE_PERMUTATION_KEY}
      JWT_SECRET: ${JWT_SECRET}
      JWT_TTL_MINUTES: 60
      ANALYTICS_ENABLED: ${ANALYTICS_ENABLED:-false}
      KAFKA_BOOTSTRAP: kafka:9092
      KAFKA_TOPIC_CLICKS: click-events
      CLICK_QUEUE_CAPACITY: 10000
      CLICKHOUSE_URL: http://clickhouse:8123
      CLICKHOUSE_USER: analytics
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
      RATE_LIMIT_CREATE_CAPACITY: 20
      RATE_LIMIT_CREATE_PER_HOUR: 100
      RATE_LIMIT_LOCAL_DIVISOR: ${URL_SERVICE_REPLICAS:-2}
      TRUSTED_PROXY_CIDRS: 172.16.0.0/12
      ALLOW_PRIVATE_TARGETS: "false"
      ALLOW_TEST_CLIENT_IP: ${ALLOW_TEST_CLIENT_IP:-false}
      ADMIN_EMAIL: ${ADMIN_EMAIL}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
    depends_on:
      migrate:          { condition: service_completed_successfully }
      postgres-replica: { condition: service_healthy, required: false }
      redis:            { condition: service_healthy, required: false }
      kafka-init:       { condition: service_completed_successfully, required: false }
      clickhouse:       { condition: service_healthy, required: false }
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8000/health/ready"]
      interval: 5s
      timeout: 2s
      retries: 12
      start_period: 10s
    stop_grace_period: 15s

  # ---------- ha ----------
  postgres-replica:
    image: postgres:16.4
    profiles: [ha]
    environment:
      PRIMARY_HOST: postgres-primary
      PGPASSWORD: ${REPLICATION_PASSWORD}
    entrypoint: ["/bin/bash", "/replica-entrypoint.sh"]
    ports: ["5433:5432"]
    volumes:
      - pg-replica-data:/var/lib/postgresql/data
      - ./infra/postgres/replica/entrypoint.sh:/replica-entrypoint.sh:ro
    depends_on:
      postgres-primary: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d urlshortener"]
      interval: 5s
      timeout: 3s
      retries: 20

  # ---------- cache ----------
  redis:
    image: redis:7.4-alpine
    profiles: [cache]
    command: >
      redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
      --save "" --appendonly no
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 2s
      retries: 10

  # ---------- analytics ----------
  kafka:
    image: apache/kafka:3.8.0
    profiles: [analytics]
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093,PLAINTEXT_HOST://0.0.0.0:29092
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092,PLAINTEXT_HOST://localhost:29092
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"
      KAFKA_HEAP_OPTS: "-Xms512m -Xmx512m"
    ports: ["29092:29092"]
    volumes:
      - kafka-data:/var/lib/kafka/data
    healthcheck:
      test: ["CMD-SHELL", "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092 >/dev/null 2>&1"]
      interval: 10s
      timeout: 10s
      retries: 12

  kafka-init:
    image: apache/kafka:3.8.0
    profiles: [analytics]
    depends_on:
      kafka: { condition: service_healthy }
    entrypoint: ["/bin/bash", "-c"]
    command:
      - |
        set -e
        T=/opt/kafka/bin/kafka-topics.sh
        $$T --bootstrap-server kafka:9092 --create --if-not-exists --topic click-events \
            --partitions 6 --replication-factor 1 --config retention.ms=86400000
        $$T --bootstrap-server kafka:9092 --create --if-not-exists --topic click-events.dlq \
            --partitions 1 --replication-factor 1 --config retention.ms=604800000
    restart: "no"

  clickhouse:
    image: clickhouse/clickhouse-server:24.8
    profiles: [analytics]
    environment:
      CLICKHOUSE_DB: analytics
      CLICKHOUSE_USER: analytics
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: 1
    ports: ["8123:8123"]
    ulimits:
      nofile: { soft: 262144, hard: 262144 }
    volumes:
      - ch-data:/var/lib/clickhouse
      - ./infra/clickhouse/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8123/ping"]
      interval: 5s
      timeout: 2s
      retries: 20

  analytics-worker:
    build: ./services/analytics-worker
    profiles: [analytics]
    environment:
      <<: *app-env
      KAFKA_BOOTSTRAP: kafka:9092
      KAFKA_TOPIC_CLICKS: click-events
      KAFKA_TOPIC_DLQ: click-events.dlq
      KAFKA_GROUP_ID: analytics-worker
      CLICKHOUSE_URL: http://clickhouse:8123
      CLICKHOUSE_USER: analytics
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
      BATCH_MAX_ROWS: 5000
      BATCH_MAX_AGE_MS: 1000
      GEOIP_DB_PATH: /geoip/GeoLite2-Country.mmdb     # optional; absent → country 'ZZ'
      METRICS_PORT: 9100
    volumes:
      - ./infra/geoip:/geoip:ro
    depends_on:
      kafka-init: { condition: service_completed_successfully }
      clickhouse: { condition: service_healthy }
    restart: unless-stopped

  # ---------- obs ----------
  prometheus:
    image: prom/prometheus:v2.54.1
    profiles: [obs]
    ports: ["9090:9090"]
    volumes:
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro

  grafana:
    image: grafana/grafana:11.2.0
    profiles: [obs]
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
    volumes:
      - ./infra/grafana/provisioning:/etc/grafana/provisioning:ro
    depends_on: [prometheus]

  redis-exporter:
    image: oliver006/redis_exporter:v1.62.0
    profiles: [obs]
    environment:
      REDIS_ADDR: redis://redis:6379

  postgres-exporter:
    image: prometheuscommunity/postgres-exporter:v0.15.0
    profiles: [obs]
    environment:
      DATA_SOURCE_NAME: postgresql://app:${POSTGRES_PASSWORD}@postgres-primary:5432/urlshortener?sslmode=disable

volumes:
  pg-primary-data:
  pg-replica-data:
  kafka-data:
  ch-data:
```

Notes:
- `url-service` has **no host port and no `container_name`**, so `--scale url-service=N` works. Nginx reaches the replicas through Docker DNS.
- `$$T` escapes `$` for Compose interpolation.
- Image tags are pinned; bump them deliberately.

### 9.4 `.env.example`

```dotenv
COMPOSE_PROFILES=cache,analytics,obs
URL_SERVICE_REPLICAS=2

POSTGRES_PASSWORD=app_local_pw
REPLICATION_PASSWORD=repl_local_pw
CLICKHOUSE_PASSWORD=ch_local_pw
GRAFANA_PASSWORD=admin

# Feature wiring — must match COMPOSE_PROFILES
REDIS_URL=redis://redis:6379/0           # empty when 'cache' profile is off
ANALYTICS_ENABLED=true                   # false when 'analytics' profile is off
DB_REPLICA_URL=                          # postgresql://app:app_local_pw@postgres-replica:5432/urlshortener with 'ha'

# Generate once with: openssl rand -hex 32   — NEVER change CODE_PERMUTATION_KEY afterwards
CODE_PERMUTATION_KEY=
JWT_SECRET=

ADMIN_EMAIL=admin@local.test
ADMIN_PASSWORD=change-me-locally

NGINX_RATE_LIMIT=50r/s                   # raise to 100000r/s for k6 load tests from one IP
ALLOW_TEST_CLIENT_IP=false
```
`.env` is git-ignored; `.env.example` is committed.

### 9.5 Nginx — `infra/nginx/templates/default.conf.template`

The official image runs `envsubst` on templates into `/etc/nginx/conf.d/` (http context). Only defined env vars are substituted, so Nginx's own `$variables` are untouched.

```nginx
resolver 127.0.0.11 valid=10s ipv6=off;        # Docker DNS; re-resolves scaled replicas

upstream url_service {
    zone url_service 64k;
    server url-service:8000 resolve max_fails=3 fail_timeout=10s;   # 'resolve' needs nginx ≥ 1.27.3
    keepalive 64;
}

limit_req_zone $binary_remote_addr zone=perip:10m rate=${NGINX_RATE_LIMIT};
limit_req_status 429;

log_format json escape=json
  '{"ts":"$time_iso8601","rid":"$request_id","ip":"$remote_addr","m":"$request_method",'
  '"uri":"$uri","st":$status,"rt":$request_time,"urt":"$upstream_response_time","up":"$upstream_addr"}';

server {
    listen 80;
    access_log /dev/stdout json;

    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Request-Id $request_id;
    proxy_connect_timeout 1s;
    proxy_read_timeout 5s;
    proxy_next_upstream error timeout http_502 http_503;     # GET/HEAD only by default
    proxy_next_upstream_tries 2;
    add_header X-Request-Id $request_id always;

    location = /nginx-health { access_log off; return 200 "ok\n"; }
    location ^~ /metrics     { return 404; }
    location ^~ /health/     { proxy_pass http://url_service; }

    location ^~ /api/ {
        limit_req zone=perip burst=100 nodelay;
        client_max_body_size 16k;
        proxy_pass http://url_service;
    }

    location ~ "^/[A-Za-z0-9_-]{4,32}$" {
        limit_req zone=perip burst=200 nodelay;
        proxy_pass http://url_service;
    }

    location / { return 404; }
}
```

### 9.6 PostgreSQL Replication Scripts

`infra/postgres/primary/01-replication.sh` (runs once at first init):
```bash
#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';"
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
```

`infra/postgres/replica/entrypoint.sh`:
```bash
#!/bin/bash
set -euo pipefail
export PGDATA=/var/lib/postgresql/data
mkdir -p "$PGDATA" && chown -R postgres:postgres "$PGDATA" && chmod 700 "$PGDATA"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "Cloning from ${PRIMARY_HOST}..."
  until gosu postgres pg_basebackup -h "$PRIMARY_HOST" -U replicator -D "$PGDATA" \
        -R -X stream -C -S replica_1 -P; do
    echo "primary not ready, retrying in 2s"; rm -rf "${PGDATA:?}"/*; sleep 2
  done
fi
exec gosu postgres postgres -c hot_standby=on
```
`-R` writes `standby.signal` and `primary_conninfo`; `-C -S replica_1` creates a replication slot so the primary keeps WAL while the replica is down.

> ⚠️ A replication slot makes the primary keep WAL forever if the replica is removed. When dropping the `ha` profile for good, run `SELECT pg_drop_replication_slot('replica_1');` on the primary, or `make nuke`.

### 9.7 Prometheus — `infra/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 5s
scrape_configs:
  - job_name: url-service
    metrics_path: /metrics
    dns_sd_configs:
      - names: [url-service]      # every scaled replica is discovered
        type: A
        port: 8000
  - job_name: analytics-worker
    dns_sd_configs:
      - names: [analytics-worker]
        type: A
        port: 9100
  - job_name: redis
    static_configs: [{ targets: ["redis-exporter:9121"] }]
  - job_name: postgres
    static_configs: [{ targets: ["postgres-exporter:9187"] }]
  - job_name: clickhouse
    static_configs: [{ targets: ["clickhouse:9363"] }]   # enable <prometheus> in CH config to use
```

### 9.8 Makefile

```makefile
up:        ; docker compose up -d --build
up-core:   ; COMPOSE_PROFILES= docker compose up -d --build
up-all:    ; COMPOSE_PROFILES=cache,analytics,ha,obs docker compose up -d --build
scale:     ; docker compose up -d --scale url-service=$(N)
down:      ; docker compose down
nuke:      ; docker compose down -v --remove-orphans
logs:      ; docker compose logs -f --tail=100 $(S)
ps:        ; docker compose ps
psql:      ; docker compose exec postgres-primary psql -U app -d urlshortener
redis-cli: ; docker compose exec redis redis-cli
ch:        ; docker compose exec clickhouse clickhouse-client -u analytics --password $$CLICKHOUSE_PASSWORD -d analytics
topics:    ; docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group analytics-worker
loadtest:  ; docker run --rm -i --network host grafana/k6:0.54.0 run - < loadtest/k6/$(T).js
```

### 9.9 Local Endpoints

| URL | What |
|---|---|
| http://localhost:8080 | App via Nginx (API + short links) |
| http://localhost:3000 | Grafana (admin / `GRAFANA_PASSWORD`) |
| http://localhost:9090 | Prometheus |
| http://localhost:8123/play | ClickHouse SQL UI |
| `localhost:5432` / `5433` | Postgres primary / replica |
| `localhost:6379` | Redis |
| `localhost:29092` | Kafka (host clients) |

### 9.10 Resource Budget (Docker Desktop ≥ 6 GB)

| Service | Approx. RAM |
|---|---:|
| url-service × 2 | 2 × 250–500 MB (runtime-dependent) |
| postgres-primary + replica | 2 × 300 MB |
| redis | 256 MB cap |
| kafka | ~700 MB (512 MB heap) |
| clickhouse | ~800 MB–1 GB |
| analytics-worker | ~200 MB |
| nginx, prometheus, grafana, exporters | ~400 MB |
| **Total (all profiles)** | **~3.5–4.5 GB** |

---

## 10. Local Verification Plan

### 10.1 Test Layers

| Layer | Scope | Runs against |
|---|---|---|
| Unit | Base62, Feistel bijectivity (exhaustive on a reduced domain + 10M random samples), validators, token-bucket maths, `decide()` | In-process |
| Integration | Repositories, Lua script, idempotency races, Kafka publish, ClickHouse insert/query | Throwaway containers (Testcontainers) or `docker compose -p urlsh-test` with the same images |
| API / contract | Every endpoint and error code in §3 and §7.1 | Full stack via Nginx on `:8080` |
| Load (k6) | `redirect.js` (95% hot / 5% cold codes), `create.js`, `mixed.js` (100:1) | Full stack, `NGINX_RATE_LIMIT=100000r/s` |
| Chaos | §10.2 | Full stack under `mixed.js` load |

Key concurrency tests:
- 50 parallel creates with the same `Idempotency-Key` → exactly 1 row in `urls`, 50 identical responses.
- 2 replicas × 100k IDs each → no duplicate IDs or codes.
- 1,000 parallel redirects of a cold code → 1 DB query per replica (single-flight).

### 10.2 Chaos Scenarios

| Action | Expected |
|---|---|
| `docker compose stop redis` | Redirects stay 302; p99 rises; `circuit_breaker_state{dependency="redis"}=2`; creates still work; rate limits fall back to local buckets |
| `docker kill url-shortener-url-service-1` | Brief 502s at most (retried by Nginx for GET); traffic shifts to the other replica; restarted replica takes a fresh ID block |
| `docker compose stop postgres-primary` | Creates/deletes → 503; cached redirects → 302; uncached → replica (with `ha`) |
| `docker compose pause postgres-replica` | Read breaker opens; reads fall back to primary |
| `docker compose stop kafka` | Redirects unaffected; `analytics_events_dropped_total` rises once the 10k queue fills |
| `docker compose stop clickhouse` | Analytics API → 503; worker pauses; consumer lag grows, then drains after restart with no loss |
| `docker compose stop analytics-worker` | Consumer lag grows; recovers on start (within 24 h topic retention) |
| Delete a link, then hit it immediately on both replicas | 410 within ≤ 30 s on every replica (usually instantly via pub/sub) |

### 10.3 Acceptance Mapping (Requirements §12)

| # | Criterion | Verified by |
|---|---|---|
| 1 | Valid URL shortened | API test `POST /api/v1/urls` → 201 |
| 2 | Codes unique | Concurrency test §10.1 + `uq_urls_short_code` |
| 3 | Redirect correct | API test `GET /{code}` → 302 + `Location` |
| 4 | Invalid URLs rejected | Validator unit tests + API 400 cases |
| 5 | Unknown code → error | 404 test |
| 6 | Expired not accessible | Create with `expiresAt = now+61s`, wait, expect 410 |
| 7 | Manage links | List/get/delete API tests |
| 8 | Click analytics recorded | Redirect N times → `/analytics` shows N within 5 s |
| 9 | Automated tests | CI runs unit + integration + API suites |
| 10 | Runs on Docker | `make up-all` from a clean clone → all healthchecks green |

---

## 11. Open Items

1. ~~Choose the language/framework~~ **Decided:** Python 3.14 + FastAPI.
2. Decide whether to commit a GeoLite2 database locally (licence requires a free MaxMind account) or keep `ZZ`-only analytics.
3. Confirm the rate-limit policy numbers (§6.8).
4. Turn §3 into `docs/api/openapi.yaml` and generate contract tests from it.

---

**End of Document**

**Next Deliverable:** OpenAPI specification, then implementation scaffolding (Compose files, migrations, service skeletons).
