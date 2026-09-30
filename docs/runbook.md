# Runbook

How the deployed services are set up, how to operate them, and what to do when
something looks wrong. For running locally, see the [README](../README.md).

## What is running

| Service | Store | Defined in |
|---|---|---|
| memory service | in-process memory | [`render.yaml`](../render.yaml) |
| Redis service | Upstash Redis (Frankfurt, TLS) | [`render.yaml`](../render.yaml) |

The service URLs are shared separately rather than published here.

- **Hosting:** both are Render free web services in Frankfurt, built from the
  same code; only `STORAGE` differs.
- **Build:** `pip install -r requirements.txt`, on Python 3.13 (from
  `.python-version`).
- **Start:** `uvicorn app.server:app --host 0.0.0.0 --port $PORT`.

## Health check and warm-up

There is no separate health endpoint; the API has exactly `/foo` and `/bar`. An
unauthenticated call answers `401` without touching any rate-limit counter:

Replace `YOUR-SERVICE-URL` with the service's address.

<!-- shells: bash -->
```bash
curl -s -o /dev/null -w "%{http_code} in %{time_total}s\n" https://YOUR-SERVICE-URL/foo
```

<!-- shells: cmd powershell -->
```bat
curl.exe -s -o NUL -w "%{http_code} in %{time_total}s\n" https://YOUR-SERVICE-URL/foo
```

`401 in 0.15s` means it is up. `401 in 55s` means it was asleep (see
[Cold starts](#cold-starts)).

Warm both services a few minutes before a demo.

## Cold starts

Free services sleep after 15 minutes without traffic. The next request wakes the
service, which takes about a minute. A browser shows Render's
"Application loading" page in the meantime.

- `curl` just waits. `scripts/demo.py` allows 90 s per request and opens its
  connections before the first burst, so it rides out a cold start.
- **After a sleep the memory service starts with empty counters.** That is
  expected, and it is the point of comparison with the Redis service, whose
  counters live in Upstash and survive restarts.

## Logs

Render dashboard → service → **Logs**. Every request produces one decision line:

```
INFO rate_limit client=client-2 endpoint=bar outcome=queued wait_ms=997 store=redis
```

| `outcome` | Meaning | HTTP |
|---|---|---|
| `allowed` | Admitted immediately | 200 |
| `queued` | `/bar` held the request for `wait_ms` before answering | 200 |
| `rejected` | Over the limit (`/foo`) or queue full (`/bar`) | 429 |
| `unauthorized` | Missing, malformed or unknown client (logged as `client=-`) | 401 |
| `store_error` | Redis failed or timed out; the full error follows in the log | 503 |

The raw `Authorization` header is never logged. Unknown clients appear as
`client=-`.

## Deploying

The Blueprint was created from the public repository URL, so Render does **not**
deploy automatically on push. To ship a change:

1. Push to `main`.
2. Render dashboard → the service → **Manual Deploy** → **Deploy latest commit**.
   Repeat for the other service if the change affects it.
3. Check the health call above, then run the demo against the service from the
   repository folder, with the environment active (every shell). It should end with
   `8/8 checks passed`:

   <!-- shells: bash cmd powershell -->
   ```shell
   python scripts/demo.py --base-url https://YOUR-SERVICE-URL
   ```

Docs-only changes don't need a deploy.

## Configuration

| Variable | Service | Value |
|---|---|---|
| `STORAGE` | both | `memory` / `redis` (set by `render.yaml`) |
| `REDIS_URL` | redis | Upstash **`rediss://`** URL; a secret, set in the Render dashboard and never committed |
| `REDIS_USE_SERVER_TIME` | redis | unset (`true`): the Lua scripts use Redis's clock |

**To change a client's limits,** edit [`config/clients.yaml`](../config/clients.yaml),
push, and redeploy both services. Invalid values stop startup with a message
naming the field.

**To rotate the Redis password:**
1. In Upstash → `rate-limiter`, reset the password.
2. In Render → the Redis service → **Environment**, update `REDIS_URL`.
3. Choose **Save, rebuild, and deploy**.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Deploy fails; log ends with `Redis not reachable at redis://…upstash.io:6379 (Connection closed by server.). Hosted Redis (e.g. Upstash) needs TLS: use rediss://.` | `REDIS_URL` starts with `redis://`. Upstash accepts TLS only. | Change the scheme to `rediss://` in Render → Environment, then save and deploy. |
| Deploy fails with `Redis not reachable at … (Timeout …)` | Wrong host or port, or Upstash unreachable | Check the URL against Upstash → `rate-limiter` → Connect. |
| Requests return `503 {"error": "rate limiter unavailable"}` | Redis failed mid-request, or didn't answer within 2 s. The limiter fails closed on purpose. | Check the Upstash status and the `store_error` lines in the logs. Requests recover on their own once Redis answers. |
| First request takes about a minute | Cold start | Expected on the free tier; see [Cold starts](#cold-starts). |
| Memory service "forgot" earlier requests | The process restarted (sleep or deploy) | Expected: counters live in the process. |
| `/bar` never returns 429 | Requests are being sent one at a time | Expected: `/bar` queues instead. Send requests concurrently (`curl -Z`, or `scripts/demo.py`). |
| Locally, every Redis connection takes 2 s (Windows) | `localhost` tries IPv6 first; the local Redis listens on IPv4 | Use `127.0.0.1`. |
| Locally, `pytest` shows 11 skipped | Local Redis is not running | `docker compose up -d redis` |
| Locally, `pytest` fails in the Redis tests with an auth or `SELECT` error | `TEST_REDIS_URL` points at a Redis that is reachable but misconfigured | Fix the URL. Upstash has only database `0`, so use `…:6379/0`. |

## Free-tier limits

| Resource | Limit | Usage here |
|---|---|---|
| Render web services | 750 instance-hours / month across the workspace, 512 MB, 0.1 CPU | Two services that sleep when idle |
| Upstash Redis | 500K commands / month, 256 MB | One `EVALSHA` per request; a demo run is under 100 commands |
