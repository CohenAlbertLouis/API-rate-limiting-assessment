---
id: 0003
type: record
title: Memory and Redis stores, each limit decision one atomic step, store chosen at startup
status: accepted
deciders: Albert-Louis Cohen
date: 2026-09-30
supersedes:
superseded-by:
---

# 0003 — Memory and Redis stores, one atomic step per decision

**Context.** The brief requires two storage strategies for the counters: one in
memory and one persistent. The naive read-compute-write lets concurrent requests
all read the same state and all be admitted.

**Decision.**
- **Algorithms are pure functions**: `(state, now, limits) → (allowed, new_state)`.
  Neither store contains algorithm logic of its own except where atomicity forces
  it (the Lua scripts).
- **Memory store:** a dict of per-client state. No lock: the read, the decision
  and the write run with no `await` between them, so asyncio cannot switch to
  another request mid-way. (Amended 2026-09-30 during implementation; the draft
  said per-key `asyncio.Lock`, which would be dead code. A test inserts an
  `await` there and watches the burst test fail.)
- **Redis store:** one Lua script per algorithm, run with `EVALSHA`. The whole
  decision is one atomic server-side step. Keys expire once a bucket would be full
  or empty again, so idle clients leave nothing behind.
- **Clock:** Lua scripts read Redis's own clock (`redis.call('TIME')`), so every app
  instance shares one clock. `now` may be passed as an argument instead. Tests use
  that, and it is the fallback if a Redis provider rejects `TIME` in scripts.
- **Store selection:** the `STORAGE=memory|redis` environment variable, read once
  at startup. A deployment runs exactly one store.
- **Verification:** the Lua scripts are property-tested against the Python
  functions (same inputs, same decisions). Burst tests check that N concurrent
  requests against capacity C admit exactly C.

**Rejected.**
- *SQLite:* truly on-disk and zero setup, but counters can't be shared across
  instances and there's no built-in expiry.
- *Postgres:* durable, but one round trip plus a row lock per request is heavy for
  this.
- *`GET`/`SET` or `WATCH`/`MULTI` in Redis:* not atomic, or needs retry loops under
  contention.
- *Choosing the store per request:* storage is infrastructure, not something the
  caller picks.

**Consequences.**
- Each algorithm exists twice, in Python and in Lua. The oracle property test is
  what keeps the two from drifting.
- Redis is "persistent" in the sense that counters survive restarts of the app,
  and of Redis itself with AOF/RDB enabled. Upstash persists to disk. The local
  Docker Redis runs with `--appendonly yes`.
- Memory counters reset whenever the process restarts, including when Render's
  free tier wakes the service. That is expected: it is the difference between the
  two strategies.

**What would make this wrong.**
- Upstash's Lua support differs from Redis's in a way the oracle tests catch
  (verified on the deployed service: the demo passes against Upstash).
- "Persistent storage" is read as strictly a disk database, which discounts
  Redis. The answer to that is the AOF/RDB configuration; SQLite remains the
  fallback option.
