---
id: 0001
type: record
title: Build the service in Python with FastAPI
status: accepted
deciders: Albert-Louis Cohen
date: 2026-09-30
supersedes:
superseded-by:
---

# 0001 — Build the service in Python with FastAPI

**Context.** The brief allows any language and framework. The service needs to
hold `/bar` requests open while they wait in a queue without tying up a worker
per request. It also has to be demonstrated and extended live.

**Decision.** Python 3.12+ with FastAPI on Uvicorn, chosen because it is
lightweight and easy to use, which makes development fastest. `redis-py` (asyncio) talks
to Redis. Tests use `pytest`, `httpx`, and Hypothesis for property-based tests.

**Rejected.**
- *TypeScript + Fastify.* A similar single-threaded async model, but it needs
  more setup (build step, typing configuration) for no gain at this size.
- *Go.* Strong concurrency, but more code for the same result.
- *TypeScript + Express.* No built-in request validation or typed plugin
  structure.

**Consequences.**
- Everything is `async`. A queued `/bar` request is an `asyncio.sleep`, not a
  blocked thread.
- The memory store needs no lock: its read-decide-write contains no `await`, so
  the event loop cannot interleave another request (a test inserts an `await`
  there and the burst test fails; see 0003).
- Hypothesis supplies property-based testing for the algorithms.

**What would make this wrong.** The service has to run as several worker
processes per instance (e.g. `uvicorn --workers 4`). Each process would then keep
its own memory store. That breaks the memory strategy's per-client limits, though
the Redis strategy is unaffected. On Render's free tier (0.1 CPU) there is no
reason to run more than one worker.
