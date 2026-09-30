"""HTTP contract: R1, R3, R6, R7, R8 (independence), R15, R16, R22, R23."""
import asyncio
import logging
import time

import httpx
import pytest
import redis

from app.algorithms import LeakyBucketLimit, TokenBucketLimit
from app.config import ClientLimits
from app.main import create_app
from app.stores import MemoryStore

CLIENTS = {
    "alpha": ClientLimits(
        foo=TokenBucketLimit(capacity=2, refill_per_second=0.5),
        bar=LeakyBucketLimit(drain_per_second=20, queue_depth=1),  # 50 ms apart, 1 may wait
    ),
    "beta": ClientLimits(
        foo=TokenBucketLimit(capacity=2, refill_per_second=0.5),
        bar=LeakyBucketLimit(drain_per_second=20, queue_depth=1),
    ),
}
ALPHA = {"Authorization": "Bearer alpha"}


def client_for(store=None):
    app = create_app(CLIENTS, store or MemoryStore())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def api():
    async with client_for() as c:
        yield c


# R1, R6
@pytest.mark.parametrize("path", ["/foo", "/bar"])
async def test_allowed_request_returns_200_success_true(api, path):
    r = await api.get(path, headers=ALPHA)
    assert r.status_code == 200
    assert r.json() == {"success": True}
    assert r.headers["content-type"] == "application/json"


async def test_only_foo_and_bar_exist(api):
    app = create_app(CLIENTS, MemoryStore())
    assert {route.path for route in app.routes} == {"/foo", "/bar"}
    assert (await api.get("/docs")).status_code == 404


async def test_only_get_is_served(api):
    assert (await api.post("/foo", headers=ALPHA)).status_code == 405


# R3, R22
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic alpha"}, {"Authorization": "Bearer "}, {"Authorization": "Bearer nobody"},
     {"Authorization": "alpha"}],
    ids=["missing", "wrong-scheme", "empty-id", "unknown-id", "no-scheme"],
)
@pytest.mark.parametrize("path", ["/foo", "/bar"])
async def test_missing_malformed_or_unknown_client_gets_401(api, path, headers):
    r = await api.get(path, headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_scheme_is_case_insensitive_but_client_id_is_exact(api):
    assert (await api.get("/foo", headers={"Authorization": "bearer alpha"})).status_code == 200
    assert (await api.get("/foo", headers={"Authorization": "Bearer ALPHA"})).status_code == 401


# R7
async def test_foo_over_limit_returns_429_with_retry_after(api):
    for _ in range(2):
        await api.get("/foo", headers=ALPHA)
    r = await api.get("/foo", headers=ALPHA)
    assert r.status_code == 429
    assert r.json() == {"error": "rate limit exceeded"}
    assert r.headers["retry-after"] == "2"  # 1 token at 0.5/s


async def test_bar_full_queue_returns_429_immediately(api):
    start = time.monotonic()
    responses = await asyncio.gather(*(api.get("/bar", headers=ALPHA) for _ in range(3)))
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 200, 429]
    rejected = next(r for r in responses if r.status_code == 429)
    assert rejected.json() == {"error": "rate limit exceeded"}
    assert int(rejected.headers["retry-after"]) >= 1
    assert time.monotonic() - start < 1


# R8 - state is per client and per endpoint
async def test_limits_are_independent_per_client_and_endpoint(api):
    for _ in range(3):
        await api.get("/foo", headers=ALPHA)
    assert (await api.get("/foo", headers={"Authorization": "Bearer beta"})).status_code == 200
    assert (await api.get("/bar", headers=ALPHA)).status_code == 200


# R15
async def test_bar_reports_how_long_each_request_was_queued():
    async with client_for(MemoryStore(clock=lambda: 0.0)) as api:  # frozen clock: exact waits
        start = time.monotonic()
        first, second = await asyncio.gather(api.get("/bar", headers=ALPHA), api.get("/bar", headers=ALPHA))
        elapsed = time.monotonic() - start
    waits = sorted(int(r.headers["x-queue-wait-ms"]) for r in (first, second))
    assert waits == [0, 50]
    assert elapsed >= 0.03  # the queued one really was held (loose: timer resolution varies)


async def test_foo_has_no_queue_header(api):
    assert "x-queue-wait-ms" not in (await api.get("/foo", headers=ALPHA)).headers


# R16
async def test_every_decision_is_logged_in_one_line(caplog):
    caplog.set_level(logging.INFO, logger="rate_limit")
    async with client_for(MemoryStore(clock=lambda: 0.0)) as api:  # frozen clock: exact waits
        await log_some_decisions(api)
    lines = [r.getMessage() for r in caplog.records if r.name == "rate_limit"]
    assert lines[0] == "client=- endpoint=foo outcome=unauthorized wait_ms=0 store=memory"
    assert lines[1] == "client=alpha endpoint=foo outcome=allowed wait_ms=0 store=memory"
    assert lines[3] == "client=alpha endpoint=foo outcome=rejected wait_ms=0 store=memory"
    bar = sorted(lines[4:])
    assert bar[0] == "client=alpha endpoint=bar outcome=allowed wait_ms=0 store=memory"
    assert bar[1] == "client=alpha endpoint=bar outcome=queued wait_ms=50 store=memory"
    assert bar[2] == "client=alpha endpoint=bar outcome=rejected wait_ms=0 store=memory"
    assert "nobody" not in " ".join(lines)  # the raw header is never logged


async def log_some_decisions(api):
    await api.get("/foo", headers={"Authorization": "Bearer nobody"})
    for _ in range(3):
        await api.get("/foo", headers=ALPHA)
    await asyncio.gather(*(api.get("/bar", headers=ALPHA) for _ in range(3)))


# R23
class BrokenStore:
    name = "redis"

    async def take_token(self, key, limit):
        raise redis.exceptions.ConnectionError("connection refused")

    async def schedule(self, key, limit):
        raise redis.exceptions.TimeoutError("timed out")

    async def close(self):
        pass


@pytest.mark.parametrize("path", ["/foo", "/bar"])
async def test_store_failure_fails_closed_with_503(path, caplog):
    caplog.set_level(logging.INFO, logger="rate_limit")
    async with client_for(BrokenStore()) as c:
        r = await c.get(path, headers=ALPHA)
    assert r.status_code == 503
    assert r.json() == {"error": "rate limiter unavailable"}
    assert any("outcome=store_error" in rec.getMessage() for rec in caplog.records)
