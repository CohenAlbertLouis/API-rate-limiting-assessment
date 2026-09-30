# Decisions

Short records of the choices that shape this project, with the alternatives that
were turned down. Each record is one screen.

A decision is changed by writing a new record that supersedes the old one. The
old one is never edited, apart from its `status` and `superseded-by` fields.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-python-fastapi.md) | Python + FastAPI | accepted |
| [0002](0002-token-bucket-foo-leaky-bucket-bar.md) | `/foo` token bucket, `/bar` leaky bucket with backpressure | accepted |
| [0003](0003-memory-and-redis-stores-atomic-per-decision.md) | Memory and Redis stores, one atomic step per decision, chosen at startup | accepted |
| [0004](0004-api-contract-bearer-auth-401.md) | API contract: bearer client-id, 401 for missing, malformed, or unknown client | accepted |
| [0005](0005-free-hosting-render-upstash.md) | Free hosting on Render + Upstash | accepted |

## Currently decided, in one paragraph

A Python/FastAPI service exposes `GET /foo` (token bucket) and `GET /bar` (leaky
bucket that queues excess requests and returns 429 only when its queue is full).
Clients are identified by `Authorization: Bearer <client-id>`; missing, malformed,
or unknown ids get 401. Counters live in memory or in Redis, chosen once at
startup; every limit decision is one atomic step in either store. Two free Render
services run the same code, one per store, with Upstash as the Redis.

## Template

```markdown
---
id: NNNN
type: record
title: <the decision, as a statement>
status: accepted | superseded
deciders: <names>
date: YYYY-MM-DD
supersedes:
superseded-by:
---

# NNNN — <title>

**Context.** What forced a choice.
**Decision.** What was chosen.
**Rejected.** Alternatives and why not.
**Consequences.** What this commits us to.
**What would make this wrong.** Conditions that could actually occur.
```
