# Rate-limited API

Two `GET` endpoints, each rate-limited per client with its own algorithm:

| Endpoint | Algorithm | Over the limit |
|---|---|---|
| `/foo` | **Token bucket**: bursts up to a capacity, refilled at a steady rate | `429` straight away |
| `/bar` | **Leaky bucket with a queue**: requests leave at a fixed rate | held until their turn; `429` only when the queue is full |

Counters live **in memory** or in **Redis**, chosen at startup. Each client's limits
are set in [`config/clients.yaml`](config/clients.yaml).

> **Read this first: `/bar` delays before it rejects.**
> A request that arrives while `/bar` is busy is *queued*, not rejected. It gets a
> `200` once its slot comes up, and the `X-Queue-Wait-Ms` header says how long it
> waited. `429` comes only when the queue is already full. So a client sending one
> request at a time is only slowed down and never gets a `429`. To see `429`, send
> requests **at the same time** ([below](#overflow-each-limit-on-purpose)).
> Setting `queue_depth: 0` makes `/bar` reject immediately instead.
> The reasoning is in [decision 0002](docs/decisions/0002-token-bucket-foo-leaky-bucket-bar.md).

**Deployed:** both stores also run in the cloud; the service URLs are shared
separately. They run on a free tier that sleeps after 15 idle minutes, so the
first request can take about a minute ([details](#deployed-services)).

## Contents

1. [Prerequisites](#prerequisites)
2. [Run it: memory store](#run-it-memory-store)
3. [Run it: Redis store](#run-it-redis-store)
4. [Call the endpoints](#call-the-endpoints)
5. [Overflow each limit on purpose](#overflow-each-limit-on-purpose)
6. [Demo script](#demo-script)
7. [Tests](#tests)
8. [Configuration](#configuration)
9. [API reference](#api-reference)
10. [Deployed services](#deployed-services)
11. [Design in brief](#design-in-brief)

For a guided walkthrough see the **[demo](docs/demo.md)**; for operating the
deployment see the **[runbook](docs/runbook.md)**.

## Prerequisites

- **Python 3.12+** (developed and deployed on 3.13)
- **Docker**, only for the Redis store and its tests
- **curl 7.84+**. Windows 10 and 11 include it as `curl.exe`.

**Which commands to use.** Commands are given for **bash** (Linux, macOS, Git
Bash) and for **Windows**. The Windows lines work unchanged in both Command
Prompt and PowerShell: they call `curl.exe` (in PowerShell plain `curl` means
something else) and send output to `NUL`. Where the two Windows shells differ,
both are shown. Copy one line at a time; they carry no comments.

Commands use `127.0.0.1`, not `localhost`. On Windows, `localhost` tries IPv6
first and each new connection to the local Redis stalls for 2 seconds.

## Run it: memory store

Get the code and install it. These lines are the same in every shell:

<!-- shells: bash cmd powershell -->
```shell
git clone https://github.com/CohenAlbertLouis/API-rate-limiting-assessment.git
cd API-rate-limiting-assessment
python -m venv .venv
```

Activate the virtual environment:

<!-- shells: bash -->
```bash
source .venv/bin/activate
```

On Git Bash for Windows the path is `.venv/Scripts/activate`.

<!-- shells: cmd -->
```bat
.venv\Scripts\activate.bat
```

In PowerShell, allow the activation script for this window first; Windows blocks
scripts by default:

<!-- shells: powershell -->
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Install and start (every shell):

<!-- shells: bash cmd powershell -->
```shell
pip install -r requirements-dev.txt
uvicorn app.server:app --port 8000
```

The server listens on `http://127.0.0.1:8000` and logs one line per decision:

```
INFO rate_limit client=client-1 endpoint=bar outcome=queued wait_ms=2000 store=memory
```

## Run it: Redis store

Start Redis 7 in Docker. It listens on 127.0.0.1:6379 only and keeps an
append-only log on disk. This line is the same in every shell:

<!-- shells: bash cmd powershell -->
```shell
docker compose up -d redis
```

Then start the server with `STORAGE=redis`:

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

In Command Prompt and PowerShell the setting stays for the rest of that window.
`set "STORAGE="` (Command Prompt) or `Remove-Item Env:STORAGE` (PowerShell) goes
back to the memory store.

Startup fails straight away, with a one-line reason, if Redis can't be reached. If
Redis fails while the service is running, requests get `503`: the limiter *fails
closed* rather than letting everything through.

## Call the endpoints

Two clients are configured: `client-1` (tight limits) and `client-2` (looser).
With the server running, in a second terminal:

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

The second call has no `Authorization` header. Missing, malformed or unknown
clients all get `401`.

## Overflow each limit on purpose

The configured limits are low, so each one overflows easily:

| Client | `/foo` (token bucket) | `/bar` (leaky bucket + queue) |
|---|---|---|
| `client-1` | 3 tokens, +1 every 2 s | 1 request leaves every 2 s, 2 may wait |
| `client-2` | 6 tokens, +1 per second | 1 request leaves per second, 4 may wait |

curl sends several requests from one command: `?n=[1-5]` makes 5 numbered copies
of the URL (the server ignores `n`), and `-Z` sends them all **at once**. Without
`-Z` they go **one after another**. With parallel requests the lines come back in
whatever order the responses finish.

**`/foo`, a burst of 5.** 3 get `200`; 2 get `429` with `Retry-After`:

<!-- shells: bash -->
```bash
curl -s -Z -o /dev/null -w "%{http_code} retry-after=%header{retry-after}\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/foo?n=[1-5]"
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -Z -o NUL -w "%{http_code} retry-after=%header{retry-after}\n" -H "Authorization: Bearer client-1" "http://127.0.0.1:8000/foo?n=[1-5]"
```

```
200 retry-after=
200 retry-after=
429 retry-after=2
200 retry-after=
429 retry-after=2
```

For `client-2` send 8 (`[1-8]`): 6 get `200`, 2 get `429`. A bucket refills
between runs, `client-1`'s in 6 seconds.

**`/bar`, 5 at once.** One goes straight through, two are held (about 2 s and
4 s), two are rejected:

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

For `client-2` send 8: one straight through, four held (1 to 4 s), three rejected.

**`/bar`, one at a time** (no `-Z`). Every request waits its turn and none is
rejected:

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

## Demo script

`scripts/demo.py` runs all of the above for both clients and checks every result.
It is the same in every shell:

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
...
8/8 checks passed
```

It first waits a few seconds so every bucket starts full, then makes a few
unauthenticated calls to open its connections (the `401`s in the server log).
Run it once per server to cover both stores. `--base-url` points it at another
server, such as a deployed one.

## Tests

The same in every shell. Without Redis running, the 11 Redis tests are skipped:

<!-- shells: bash cmd powershell -->
```shell
docker compose up -d redis
pytest
```

The 86 tests cover:

- **Algorithms:** example tests plus Hypothesis property tests. Tokens stay within
  capacity; the number admitted never exceeds capacity + rate × time; `/bar`
  releases are at least one interval apart; waits never exceed the queue bound.
- **Lua matches Python:** 1,000 generated request timelines per algorithm, and the
  Redis scripts must make exactly the decisions the Python functions make.
- **Concurrency:** 50 simultaneous requests against capacity 7 admit exactly 7, in
  both stores, 20 runs each. A naive read-then-write admits all 50.
- **HTTP contract:** status codes, bodies and headers; one log line per decision;
  `503` when Redis fails or stops answering.
- **Config and startup:** invalid values rejected by field name; clear errors when
  Redis can't be reached.
- **The demo script:** it catches servers that limit wrongly.

## Configuration

`config/clients.yaml`, read at startup (path overridable with `CONFIG_PATH`):

```yaml
clients:
  client-1:
    foo: { capacity: 3, refill_per_second: 0.5 }       # token bucket
    bar: { drain_per_second: 0.5, queue_depth: 2 }     # leaky bucket + queue
  client-2:
    foo: { capacity: 6, refill_per_second: 1 }
    bar: { drain_per_second: 1, queue_depth: 4 }
```

To add a client, add an entry. It needs both `foo` and `bar`. Invalid values stop
startup with a message naming the field, for example
`clients.client-3.foo.capacity: must be an integer >= 1`.

| Environment variable | Default | Meaning |
|---|---|---|
| `STORAGE` | `memory` | `memory` or `redis` |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Use `rediss://` (TLS) for hosted Redis such as Upstash |
| `REDIS_USE_SERVER_TIME` | `true` | The Lua scripts read Redis's clock, so every instance shares one. `false` uses the app's clock |
| `CONFIG_PATH` | `config/clients.yaml` | Client limits file |

## API reference

`Authorization: Bearer <client-id>` is required on both endpoints. The scheme is
case-insensitive; the client id must match exactly.

| Situation | Status | Body | Headers |
|---|---|---|---|
| Allowed | `200` | `{"success": true}` | `/bar`: `X-Queue-Wait-Ms` |
| Over the limit (`/foo`) or queue full (`/bar`) | `429` | `{"error": "rate limit exceeded"}` | `Retry-After` (seconds) |
| Missing, malformed or unknown client | `401` | `{"error": "unauthorized"}` | `WWW-Authenticate: Bearer` |
| Redis error or timeout (Redis store) | `503` | `{"error": "rate limiter unavailable"}` | |

The brief's text shows `{ succes:true }` while its example shows
`"success": true`. This API follows the example
([decision 0004](docs/decisions/0004-api-contract-bearer-auth-401.md)).

## Deployed services

| Store | Where the counters live |
|---|---|
| Memory | in the service process |
| Redis | Upstash (managed Redis, TLS) |

Both run on Render's free tier from [`render.yaml`](render.yaml). The same code is
deployed twice and only `STORAGE` differs. The URLs are shared separately rather
than published here.

- **Cold start:** a service that has been idle for 15 minutes takes about a minute
  to answer its first request.
- **The memory store starts empty after every sleep.** Counters live in the
  process. The Redis service keeps its counters across restarts, which is the
  difference between the two strategies.

### Try the live API

Only `curl` is needed. In each command below, replace `YOUR-SERVICE-URL` with the
address you were given (for example `abc.example.com`, without `https://`).
Over the internet, keep `-Z` so each burst arrives at once; one request at a time
is slow enough for tokens to refill in between.

**Wake the service and check it answers.** Expect `401` (no client given); this
touches no rate-limit counters:

<!-- shells: bash -->
```bash
curl -s -w " %{http_code}\n" https://YOUR-SERVICE-URL/foo
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -w " %{http_code}\n" https://YOUR-SERVICE-URL/foo
```

**`/foo`, a burst of 8 for `client-2`:** 6 × `200`, 2 × `429`.

<!-- shells: bash -->
```bash
curl -s -Z -o /dev/null -w "%{http_code} retry-after=%header{retry-after}\n" -H "Authorization: Bearer client-2" "https://YOUR-SERVICE-URL/foo?n=[1-8]"
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -Z -o NUL -w "%{http_code} retry-after=%header{retry-after}\n" -H "Authorization: Bearer client-2" "https://YOUR-SERVICE-URL/foo?n=[1-8]"
```

**`/bar`, 5 at once for `client-1`:** one straight through, two held, two
rejected.

<!-- shells: bash -->
```bash
curl -s -Z -o /dev/null -w "%{http_code} queued=%header{x-queue-wait-ms}ms\n" -H "Authorization: Bearer client-1" "https://YOUR-SERVICE-URL/bar?n=[1-5]"
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -Z -o NUL -w "%{http_code} queued=%header{x-queue-wait-ms}ms\n" -H "Authorization: Bearer client-1" "https://YOUR-SERVICE-URL/bar?n=[1-5]"
```

```
200 queued=0ms
429 queued=ms
429 queued=ms
200 queued=1934ms
200 queued=3888ms
```

The held requests wait slightly under 2 s and 4 s here: each wait counts from that
request's own arrival, and the parallel requests reach the server a few
milliseconds apart.

**All checks at once**, from the repository folder with the environment active
(every shell):

<!-- shells: bash cmd powershell -->
```shell
python scripts/demo.py --base-url https://YOUR-SERVICE-URL
```

## Design in brief

- **Each algorithm is a pure function**: `(state, now, limit) → (decision, new state)`,
  in [`app/algorithms.py`](app/algorithms.py). There is no I/O and no clock, so both
  stores share one definition of "correct" and tests control time.
- **`/bar` needs no real queue.** Each client stores one number, `next_free`, the
  time the next request may leave. A request takes the slot `max(now, next_free)`
  and sleeps until then, or is rejected if that wait is longer than a full queue
  would take.
- **Each decision is atomic.** In memory, the read, decision and write contain no
  `await`, so asyncio can't interleave two requests (a test inserts one and watches
  the burst test fail). In Redis, each decision is one Lua script
  ([`app/lua/`](app/lua)), which Redis runs atomically.
- **Fail closed.** If Redis errors or doesn't answer within 2 s, the request gets
  `503`, never a free pass.

```
app/algorithms.py      token bucket + leaky bucket, pure functions
app/stores.py          MemoryStore, RedisStore (one atomic call per decision)
app/lua/*.lua          the same two algorithms, run inside Redis
app/main.py            HTTP layer: auth -> store decision -> response
app/config.py          YAML limits, STORAGE / REDIS_URL, startup checks
app/server.py          entry point: uvicorn app.server:app
scripts/demo.py        both clients x both endpoints, with checks
```

More detail:

- [Decision records](docs/decisions/README.md): stack, algorithms, storage, API contract, hosting.
- [Specification](docs/specs/spec-rate-limited-api-2026-09-30.md): requirements, each traced to the brief and to tests.
- [Demo](docs/demo.md): a 15-minute walkthrough with the expected output of every step.
- [Runbook](docs/runbook.md): operating the deployed services, logs, redeploys, troubleshooting.
