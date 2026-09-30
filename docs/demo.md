# Demo

A 15-minute walkthrough: both clients, both endpoints, both storage strategies,
locally and deployed. Each step lists the command and the output to expect.

Commands are given for **bash** (Linux, macOS, Git Bash) and for **Windows**. The
Windows lines work unchanged in Command Prompt and PowerShell. Where those two
differ, both are shown. Copy one line at a time.

| Time | Step | Shows |
|---|---|---|
| 0:00 | [1. Overview](#1-overview-1-min) | What the API does, and the `/bar` rule |
| 1:00 | [2. Memory store, by hand](#2-memory-store-by-hand-4-min) | 200 / 401 / 429, token-bucket burst, leaky-bucket queue |
| 5:00 | [3. Redis store](#3-redis-store-3-min) | Same behaviour, counters that survive a restart |
| 8:00 | [4. Deployed services](#4-deployed-services-2-min) | 16/16 checks against the deployed services |
| 10:00 | [5. Tests](#5-tests-2-min) | 86 tests: properties, Lua-vs-Python oracle, concurrency |
| 12:00 | [6. Code tour](#6-code-tour-3-min) | Where each idea lives |

## Before you start

About 10 minutes ahead, in the repository folder:

1. Start Docker Desktop, then start Redis (every shell):

   <!-- shells: bash cmd powershell -->
   ```shell
   docker compose up -d redis
   ```

2. Activate the virtual environment in **both** terminals you will use. See the
   [README](../README.md#run-it-memory-store) for Command Prompt and PowerShell.

   <!-- shells: bash -->
   ```bash
   source .venv/bin/activate
   ```

   On Git Bash for Windows the path is `.venv/Scripts/activate`.

3. Wake the deployed services; each should answer `401`. Replace
   `YOUR-MEMORY-SERVICE-URL` and `YOUR-REDIS-SERVICE-URL` with the two addresses:

   <!-- shells: bash -->
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" https://YOUR-MEMORY-SERVICE-URL/foo
   curl -s -o /dev/null -w "%{http_code}\n" https://YOUR-REDIS-SERVICE-URL/foo
   ```

   <!-- shells: cmd powershell -->
   ```bat
   curl.exe -s -o NUL -w "%{http_code}\n" https://YOUR-MEMORY-SERVICE-URL/foo
   curl.exe -s -o NUL -w "%{http_code}\n" https://YOUR-REDIS-SERVICE-URL/foo
   ```

Open these files in the editor: `app/algorithms.py`, `app/stores.py`,
`app/lua/leaky_bucket.lua`, `app/main.py`, `config/clients.yaml`.

## 1. Overview (1 min)

Show the top of the README:
- `/foo` is a token bucket.
- `/bar` is a leaky bucket with a queue.
- The store is memory or Redis, chosen at startup.
- Limits come from `config/clients.yaml`.

Say the one rule people trip over: **`/bar` delays before it rejects.**

## 2. Memory store, by hand (4 min)

**Terminal 1** (every shell):

<!-- shells: bash cmd powershell -->
```shell
uvicorn app.server:app --port 8000
```

**Terminal 2.** Allowed, then unauthorized:

<!-- shells: bash -->
```bash
curl -s -w " %{http_code}\n" -H "Authorization: Bearer client-1" http://127.0.0.1:8000/foo
curl -s -w " %{http_code}\n" http://127.0.0.1:8000/foo
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -w " %{http_code}\n" -H "Authorization: Bearer client-1" http://127.0.0.1:8000/foo
curl.exe -s -w " %{http_code}\n" http://127.0.0.1:8000/foo
```

```
{"success":true} 200
{"error":"unauthorized"} 401
```

**Token bucket.** `client-1` has 3 tokens, refilled 1 every 2 s. Wait 6 seconds
so the bucket is full, then send 5 at once:

<!-- shells: bash -->
```bash
curl -s -Z -o /dev/null -w "%{http_code} retry-after=%header{retry-after}\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/foo?n=[1-5]"
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -Z -o NUL -w "%{http_code} retry-after=%header{retry-after}\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/foo?n=[1-5]"
```

Three `200`, two `429` with `Retry-After: 2`, in whatever order they finish:

```
200 retry-after=
200 retry-after=
429 retry-after=2
200 retry-after=
429 retry-after=2
```

**Leaky bucket, 5 at once.** `client-1` lets 1 request leave every 2 s and lets 2
wait:

<!-- shells: bash -->
```bash
curl -s -Z -o /dev/null -w "%{http_code} queued=%header{x-queue-wait-ms}ms\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/bar?n=[1-5]"
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -Z -o NUL -w "%{http_code} queued=%header{x-queue-wait-ms}ms\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/bar?n=[1-5]"
```

```
200 queued=0ms
429 queued=ms
429 queued=ms
200 queued=1999ms
200 queued=3999ms
```

**Leaky bucket, one at a time** (the same command without `-Z`, 3 requests). The
client is slowed down, never rejected:

<!-- shells: bash -->
```bash
curl -s -o /dev/null -w "%{http_code} queued=%header{x-queue-wait-ms}ms\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/bar?n=[1-3]"
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -o NUL -w "%{http_code} queued=%header{x-queue-wait-ms}ms\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/bar?n=[1-3]"
```

```
200 queued=0ms
200 queued=1999ms
200 queued=1981ms
```

Point at terminal 1. Each decision is one log line:

```
INFO rate_limit client=client-1 endpoint=bar outcome=queued wait_ms=1999 store=memory
INFO rate_limit client=client-1 endpoint=bar outcome=rejected wait_ms=0 store=memory
```

## 3. Redis store (3 min)

**Terminal 1.** Stop the server (Ctrl+C), then start it with the Redis store:

<!-- shells: bash -->
```bash
STORAGE=redis uvicorn app.server:app --port 8000
```

<!-- shells: cmd -->
```bat
set "STORAGE=redis"
uvicorn app.server:app --port 8000
```

<!-- shells: powershell -->
```powershell
$env:STORAGE = "redis"
uvicorn app.server:app --port 8000
```

**Terminal 2.** The demo script covers both clients and both endpoints and checks
every result (every shell):

<!-- shells: bash cmd powershell -->
```shell
python scripts/demo.py
```

```
client-1  /foo  200
client-1  /foo  200
client-1  /foo  200
client-1  /foo  429  retry-after 2s
client-1  /foo  429  retry-after 2s
client-1  /bar  200  waited 0.0s
client-1  /bar  200  waited 2.0s
client-1  /bar  200  waited 4.0s
client-1  /bar  429  (queue full)
client-1  /bar  429  (queue full)

client-2  /foo  200            (x6)
client-2  /foo  429  retry-after 1s
client-2  /foo  429  retry-after 1s
client-2  /bar  200  waited 0.0s
client-2  /bar  200  waited 1.0s
client-2  /bar  200  waited 2.0s
client-2  /bar  200  waited 3.0s
client-2  /bar  200  waited 4.0s
client-2  /bar  429  (queue full)
client-2  /bar  429  (queue full)

8/8 checks passed
```

**Counters survive a restart.** Spend `client-1`'s tokens with 3 requests:

<!-- shells: bash -->
```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/foo?n=[1-3]"
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -o NUL -w "%{http_code}\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/foo?n=[1-3]"
```

Look at the stored state (every shell):

<!-- shells: bash cmd powershell -->
```shell
docker compose exec redis redis-cli HGETALL rl:foo:client-1
```

```
1) "tokens"
2) "0.077075004577636719"
3) "updated_at"
4) "1790787429.031425"
```

Restart terminal 1 (Ctrl+C, then the same start command), then run the `HGETALL`
again: the same two values are still there. With the memory store they would be
gone. The next request may still get `200`, because tokens keep refilling (0.5
per second for `client-1`) while the server restarts. Keys expire by themselves
once a bucket would be full again.

## 4. Deployed services (2 min)

The same code runs twice on Render, one service per store. Run the demo against
each (every shell; replace the placeholders with the two addresses):

<!-- shells: bash cmd powershell -->
```shell
python scripts/demo.py --base-url https://YOUR-MEMORY-SERVICE-URL
python scripts/demo.py --base-url https://YOUR-REDIS-SERVICE-URL
```

Each ends with `8/8 checks passed`, 16/16 across both. The Redis service's
counters live in Upstash (managed Redis, TLS). Its logs in the Render dashboard
show one `rate_limit` line per decision.

If a service has been idle for 15 minutes, its first request takes about a
minute (see the [runbook](runbook.md#cold-starts)).

## 5. Tests (2 min)

Every shell:

<!-- shells: bash cmd powershell -->
```shell
pytest -q
```

```
86 passed in 20s
```

Point at three of them:
- `tests/test_stores.py::test_burst_of_50_concurrent_requests_admits_exactly_capacity`:
  50 simultaneous requests against capacity 7 admit exactly 7, in both stores, 20
  runs each. A naive read-then-write admits all 50.
- `tests/test_redis_oracle.py`: Hypothesis generates 1,000 request timelines per
  algorithm, and the Lua scripts must decide exactly as the Python functions do.
- `tests/test_leaky_bucket.py`: properties such as "releases are at least one
  interval apart" and "no wait exceeds the queue bound", checked on generated
  inputs.

## 6. Code tour (3 min)

In this order:
1. **`app/algorithms.py`:** both algorithms as pure functions,
   `(state, now, limit) → (decision, new state)`. `/bar` stores one number per
   client, `next_free`; there is no real queue.
2. **`app/stores.py`:** `MemoryStore` (no lock needed: no `await` between read
   and write) and `RedisStore` (one Lua script per decision).
3. **`app/lua/leaky_bucket.lua`:** the same rule as the Python, atomic inside
   Redis, using Redis's own clock.
4. **`app/main.py`:** authenticate, ask the store, answer; `503` if Redis fails
   (fail closed).
5. **`config/clients.yaml`:** where limits live. Adding a client is one entry.

Why each choice was made, and what was rejected: [decision records](decisions/README.md).
What was specified: [spec](specs/spec-rate-limited-api-2026-09-30.md).

## If something goes wrong

| Problem | Do this |
|---|---|
| Deployed service is slow to answer | It was asleep; the first request wakes it in about a minute. Carry on locally meanwhile. |
| `/foo` shows fewer 200s than expected | The bucket wasn't full; wait 6 s (`client-1`) and repeat. `demo.py` waits by itself. |
| Redis tests skipped, or `STORAGE=redis` won't start | `docker compose up -d redis`. |
| Every local Redis call takes 2 s (Windows) | Use `127.0.0.1`, not `localhost`. |
| PowerShell says running scripts is disabled | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window, then activate again. |
| `curl` in PowerShell prints an object instead of a status | Type `curl.exe`. |
