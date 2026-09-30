---
spec: rate-limited-api
title: Rate-limited API — /foo (token bucket) and /bar (leaky bucket with backpressure)
date: 2026-09-30
status: active
sources:
  - Assignment brief (not included in this repository)
  - docs/decisions/0001-python-fastapi.md
  - docs/decisions/0002-token-bucket-foo-leaky-bucket-bar.md
  - docs/decisions/0003-memory-and-redis-stores-atomic-per-decision.md
  - docs/decisions/0004-api-contract-bearer-auth-401.md
  - docs/decisions/0005-free-hosting-render-upstash.md
---

# Rate-limited API

**Status:** active · **Date:** 2026-09-30 · **Supersedes:** none

> Every requirement traces to a source above. Anything not decided there is
> marked **OPEN** or labelled *(recommendation)*.

---

## 1. Problem

Build an API that demonstrates throttling. It has two GET endpoints, each
rate-limited per client with a different algorithm. Counters can be kept in
memory or in persistent storage. It must be runnable from the README,
demonstrable live for two clients on both endpoints with both stores, and
optionally deployed. **Source:** brief.

## 2. Goals / non-goals

**Goals:** every brief requirement is met and traced to a test. Limit decisions
are correct under concurrent requests in both stores. Anyone can run, demo,
and understand it from the README alone.

**Non-goals:**

- Any endpoint beyond `/foo` and `/bar`, including health checks, metrics, and
  admin endpoints. Warm-up uses an unauthenticated `/foo` call, which returns 401
  and touches no counters.
- Changing limits at runtime. Limits are read at startup.
- Running more than one worker process per instance (see 0001).
- Distributed consistency beyond what a single Redis provides.

## 3. Design

### Requirements

| ID | Requirement | Source |
|---|---|---|
| R1 | The service exposes `GET /foo` and `GET /bar`. | Brief |
| R2 | Rate-limiting logic is our own; no rate-limiting library is used. | Brief |
| R3 | The client is identified by `Authorization: Bearer <client-id>`. The scheme is case-insensitive and the id exact. Missing, malformed, or unknown → 401 `{"error": "unauthorized"}`. | Brief; 0004 |
| R4 | `/foo` applies a token bucket per client (§ Algorithms). | Brief; 0002 |
| R5 | `/bar` applies a leaky bucket with a bounded queue per client (§ Algorithms). Queued requests are delayed, not rejected. | Brief; 0002 |
| R6 | An allowed request returns 200 `{"success": true}`. | Brief (curl example); 0004 |
| R7 | A rejected request returns 429 `{"error": "rate limit exceeded"}` with `Retry-After` in whole seconds, at least 1. | Brief; 0004 |
| R8 | Limits are configurable per client and per endpoint in a YAML file (§ Config). `/foo` and `/bar` state is independent. | Brief; 0004 |
| R9 | The shipped config defines `client-1` and `client-2` with different limits (§ Config). | Brief |
| R10 | An in-memory store keeps counters in process. | Brief; 0003 |
| R11 | A Redis store keeps counters in Redis. Each decision is one Lua script execution. | Brief; 0003 |
| R12 | `STORAGE=memory\|redis` (default `memory`) selects the store once at startup. | 0003 |
| R13 | Under N concurrent requests against capacity C with no refill, exactly min(N, C) are allowed, in both stores. | 0003 |
| R14 | For any sequence of (time, request), the Lua scripts and the Python functions make identical decisions. | 0003 |
| R15 | Every `/bar` 200 carries `X-Queue-Wait-Ms` (integer ms, `0` if not queued). | 0002; 0004 |
| R16 | One log line per request decision, in the format under § Logging. | 0002 |
| R17 | An automated test suite covers R1–R16. | Brief ("at least 1 test") |
| R18 | `README.md` is the run-and-demo tutorial (§ README). | Brief |
| R19 | `scripts/demo.py` exercises 2 clients × 2 endpoints and reports 200s and 429s per request. | Brief ("demonstrate") |
| R20 | `render.yaml` defines two free Render services (memory, redis). The Redis one uses Upstash through the `REDIS_URL` secret. | Brief (stretch); 0005 |
| R22 | A 401 carries `WWW-Authenticate: Bearer`. *(recommendation: required by HTTP semantics, RFC 9110 §11.6.1)* | Recommendation |
| R23 | With `STORAGE=redis`, a Redis error during a decision fails closed: 503 `{"error": "rate limiter unavailable"}`, logged with `outcome=store_error`. | Albert-Louis, 2026-09-30 (resolved OPEN) |

### Algorithms (pure functions; `now` in seconds, float)

`Decision = (allowed: bool, wait: float, retry_after: float)`

**Token bucket** (`/foo`). Limit: `capacity` (int ≥ 1), `refill_per_second`
(float > 0). State: `(tokens, updated_at)`. There is no state before a client's
first request, so the bucket starts full.

```
now    = max(now, updated_at)                       # a clock going backwards neither adds nor removes
tokens = min(capacity, tokens + (now - updated_at) * refill_per_second)
if tokens >= 1 - 1e-9:  tokens -= 1  → allowed, wait 0   # 1e-9 absorbs float rounding
else:            → rejected, retry_after = (1 - tokens) / refill_per_second
new state = (tokens, now)                          # updated on reject too; never rewound
```

**Leaky bucket with queue** (`/bar`), by virtual scheduling. Limit:
`drain_per_second` (float > 0) and `queue_depth` (int ≥ 0). `interval =
1 / drain_per_second` and `max_wait = queue_depth × interval`. State:
`next_free`; with no state, `next_free = now`.

```
slot = max(now, next_free);  wait = slot - now
if wait <= max_wait + 1e-9:  next_free = slot + interval → allowed, wait
else:                        → rejected, retry_after = wait - max_wait; state unchanged
```

Worked example: `client-1` sends 5 concurrent requests at t = 0. The waits are
0, 2, 4, then two rejections (a 6 s wait exceeds the 4 s limit). `queue_depth: 0`
gives a strict meter.

### Interfaces / contracts

**HTTP.**

| Request | Response |
|---|---|
| `GET /foo` or `/bar`, valid client, allowed | 200 `{"success": true}`; `/bar` adds `X-Queue-Wait-Ms` |
| Valid client, rejected | 429 `{"error": "rate limit exceeded"}`, `Retry-After: <ceil(retry_after), ≥1>` |
| Header missing, not `Bearer <id>`, empty id, or unknown id | 401 `{"error": "unauthorized"}`, `WWW-Authenticate: Bearer` |
| Other methods on `/foo`, `/bar` | 405 (framework default) |

All bodies are `application/json`. Authentication runs before rate limiting
(0004).

**Store interface** (`app/stores/base.py`):

```python
class Store(Protocol):
    async def take_token(self, key: str, limit: TokenBucketLimit) -> Decision: ...
    async def schedule(self, key: str, limit: LeakyBucketLimit) -> Decision: ...
    async def close(self) -> None: ...
```

- **Key:** `rl:{endpoint}:{client_id}`, for example `rl:foo:client-1`.
- **Memory store:** a dict of states, no lock (read-decide-write has no `await`,
  so asyncio cannot interleave requests). It is `time.monotonic()` by default and
  takes an injectable clock.
- **Redis store:** `token_bucket.lua` and `leaky_bucket.lua`, loaded once and run
  with `EVALSHA` (falling back to `EVAL` on `NOSCRIPT`).
  - `now` comes from `redis.call('TIME')` unless the caller passes it as `ARGV`,
    which tests do and which is the fallback (0003).
  - Keys expire (`PEXPIRE`) 1 s after the bucket would be full, or after
    `next_free` has passed.
  - The endpoint awaits `asyncio.sleep(wait)` *after* the store decision, so the
    queue holds no store resources.

### Config

`CONFIG_PATH` (default `config/clients.yaml`), validated at startup. Invalid
config stops the process with a message naming the field.

```yaml
clients:
  client-1:                                   # tight
    foo: { capacity: 3, refill_per_second: 0.5 }
    bar: { drain_per_second: 0.5, queue_depth: 2 }
  client-2:                                   # loose
    foo: { capacity: 6, refill_per_second: 1 }
    bar: { drain_per_second: 1, queue_depth: 4 }
```

**Environment variables:**
- `STORAGE`: `memory` (the default) or `redis`.
- `REDIS_URL`: defaults to `redis://127.0.0.1:6379/0`.
- `REDIS_USE_SERVER_TIME`: defaults to `true`.
- With `STORAGE=redis`, startup `PING`s Redis and exits if it is unreachable.

### Logging

One `INFO` line per request: `rate_limit client=<id> endpoint=<foo|bar>
outcome=<allowed|queued|rejected|unauthorized> wait_ms=<int>
store=<memory|redis>`. For `unauthorized`, `client=-`; the raw header is never
logged.

### Error and edge behaviour

- **Concurrency:** R13. Read, compute and write run with no `await` between them (memory) or inside one Lua script (Redis).
- **Client disconnects while queued:** its slot stays consumed. The virtual
  schedule cannot give time back. This is documented and accepted.
- **Clock skew between app instances:** avoided by Redis `TIME`. Memory is
  per-process by definition.
- **Redis unreachable at runtime:** fail closed, 503 (R23).
- **Idle clients:** memory state is bounded by the number of configured clients
  × 2, since auth rejects unknown ids first. Redis keys expire.

## 4. Alternatives considered

Recorded in decisions 0001–0005 (algorithms, stores, contract, hosting).
Spec-level alternatives:

| Option | Why not |
|---|---|
| A real queue (list plus worker) for `/bar` | Needs a background consumer, and it is harder to make atomic in Redis. Virtual scheduling gives the same timing from one stored number. |
| Sleeping inside the store call or script | Would serialise every request for that client behind the sleeper. |
| A `/health` endpoint for warm-up | Adds surface beyond the brief; an unauthenticated `/foo` returns 401 just as cheaply. |

## 5. Open decisions

None. Resolved 2026-09-30 by Albert-Louis: Redis failure at runtime fails closed (R23). Fail-open was rejected because a limiter that silently stops limiting is the harder bug to notice.

## 6. Acceptance

Tests live under `tests/`. Redis tests use local Docker Redis and are skipped,
with a reason printed, when it is unreachable.

- [ ] R3, R6, R7, R15, R22, R23 — `tests/test_api.py` (status codes, bodies, headers).
- [ ] R4, R5 — `tests/test_algorithms.py`: Hypothesis properties.
  - Tokens stay in [0, capacity].
  - Allowed count in any window ≤ capacity + rate × elapsed.
  - `/bar` release times are non-decreasing and ≥ `interval` apart.
  - Waits never exceed `max_wait`.
- [ ] R13 — `tests/test_concurrency.py`: burst of 50 against each store, 20 runs, allowed == capacity.
- [ ] R14 — `tests/test_redis_oracle.py`: ≥ 1,000 generated timelines per algorithm, 0 divergences.
- [ ] R8, R9, R12 — `tests/test_config.py`: valid config loads, invalid config fails naming the field, `STORAGE` switches store.
- [ ] R16 — log line asserted in `tests/test_api.py`.
- [ ] R19 — `python scripts/demo.py` passes 8/8 checks against a local server per store (16/16 across both).
- [ ] R20 — the same script passes 16/16 against the two deployed URLs.
- [ ] R18 — a fresh clone reaches a passing demo from the README alone in under 10 minutes.

**Demo script behaviour (R19).** For each client it reads the config and waits
until the buckets would be full. It then checks:
- `/foo`: sequential requests; the first `capacity` return 200 and at least one
  429 follows.
- `/bar`: `1 + queue_depth + 2` concurrent requests; exactly `1 + queue_depth`
  return 200, with increasing `X-Queue-Wait-Ms`, and the rest return 429.

It prints one line per request (`200 (waited 2.0s)`, `429 retry-after 2s`) and a
summary line. `--base-url` defaults to `http://127.0.0.1:8000`.

## 7. Impact

A new repository with no existing users. Two free Render services and one
Upstash database are created (0005).

**README (R18).** It is organised as the tutorial, in this order:

1. Read this first: `/bar` semantics, with one concurrent command.
2. Prerequisites: Python 3.12+, Docker.
3. Quick start, memory store.
4. Quick start, Redis store: `docker compose up -d redis`.
5. Calling the endpoints with curl (bash and PowerShell).
6. Overflowing each limit on purpose, per client.
7. Running the demo script.
8. Running the tests.
9. Configuration reference.
10. Deployed URLs and the cold-start note.
11. Design in brief, linking decisions and this spec.

Every local command in the README uses `127.0.0.1`, never `localhost`: the dev
Redis listens on IPv4 only, and on Windows `localhost` tries IPv6 first and
stalls 2 s per connection.

## 8. Out of scope

- Per-IP limiting.
- Tiers or plans beyond the configured clients.
- Hot reloading of config.
- Persistent storage other than Redis.
- Horizontal scaling tests.
- Authentication beyond matching the client-id (no secrets or signatures).
  The brief's bearer value is an identifier, not a credential.

---

## Change log

| Date | Change |
|---|---|
| 2026-09-30 | Created |
| 2026-09-30 | Amended after review: local defaults use 127.0.0.1 (IPv4) instead of localhost; Redis client has 2 s timeouts (R23); rates must be finite |
| 2026-09-30 | Amended during implementation (findings from tests): token bucket never rewinds `updated_at` (a test caught double-counted seconds after a backwards clock step); 1e-9 tolerance (Hypothesis counterexample 4/3 s × 0.75/s = 0.9999… tokens); memory store needs no lock |
| 2026-09-30 | Resolved the Redis-failure OPEN (fail closed, R23); status draft → active before any implementation |
