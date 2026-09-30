---
id: 0002
type: record
title: /foo uses a token bucket; /bar uses a leaky bucket with backpressure
status: accepted
deciders: Albert-Louis Cohen
date: 2026-09-30
supersedes:
superseded-by:
---

# 0002 — `/foo` token bucket, `/bar` leaky bucket with backpressure

**Context.** The brief requires a different rate-limiting algorithm on each
endpoint. One catch: if both endpoints reject over-limit requests immediately, a
leaky bucket used as a meter makes the same admit/reject decisions as a token
bucket. That would be the same algorithm twice under two names.

**Decision.**
- **`/foo`, token bucket.** Each client has a bucket of `capacity` tokens, refilled
  continuously at `refill_rate` per second. A request takes one token if one is
  available (200), otherwise it gets 429 at once. Bursts up to `capacity` are
  allowed.
- **`/bar`, leaky bucket as a queue (backpressure).** Requests leave the bucket at
  a fixed `drain_rate`. A request arriving while the bucket is draining is held
  until its turn, then gets 200. It gets 429 at once only when `queue_depth`
  requests are already waiting. The output rate is smooth, with no bursts.
- It is implemented as virtual scheduling. Per client we store the time the next
  request may leave (`next_free`). An arriving request takes the slot
  `max(now, next_free)`. If the wait exceeds `queue_depth × drain_interval`, it is
  rejected; otherwise the slot is stored and the request sleeps until it arrives.
  There is no real queue object, so the scheme works the same in memory and in
  Redis.

**Rejected.**
- *Leaky bucket as a meter (immediate 429).* Matches the brief's wording, but in
  practice it is the token bucket again.
- *Fixed or sliding window on `/bar`.* Distinct, but it drops the smoothing
  property that makes the two endpoints worth comparing.

**Consequences.**
- `/bar` departs from the brief's literal "429 when the rate limit is reached".
  Here "limit reached" means *queue full*. So that a delayed 200 explains itself
  where a caller sees it, the queueing is made visible in four places:
  1. every `/bar` response carries `X-Queue-Wait-Ms`, the time it was held (`0` if
     it wasn't);
  2. the server logs one line per decision (`outcome=queued wait_ms=2000`,
     `outcome=rejected`);
  3. the README opens with a "Read this first" note and a concurrent-request
     command;
  4. the demo script prints status and wait time for every request.
- A client sending one request at a time never gets 429 from `/bar`; it is simply
  slowed down. Overflowing it needs concurrent requests, which the README demo
  shows.
- Waits are bounded by `queue_depth × drain_interval`. With the demo limits that
  is at most 4 s.
- `queue_depth: 0` gives a strict meter (immediate 429). This is supported for
  anyone who wants the literal reading.

**What would make this wrong.** Callers treat any delayed 200 as a failed
requirement, whatever the README says. Or a hosting proxy times out queued
requests, which happens if queue bounds are ever configured beyond the proxy's
timeout.
