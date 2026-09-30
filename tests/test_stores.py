"""R10 memory store, R11 Redis store, R13 atomic decisions under concurrency."""
import asyncio
import uuid

import pytest
import redis.asyncio as aioredis

from app.algorithms import LeakyBucketLimit, TokenBucketLimit
from app.stores import MemoryStore, RedisStore

FOO = TokenBucketLimit(capacity=3, refill_per_second=0.5)
BAR = LeakyBucketLimit(drain_per_second=0.5, queue_depth=2)
NO_REFILL = TokenBucketLimit(capacity=7, refill_per_second=1e-6)


def key():
    return f"test:{uuid.uuid4()}"


@pytest.fixture(params=["memory", "redis"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    client = aioredis.Redis.from_url(request.getfixturevalue("redis_url"))
    s = RedisStore(client)
    yield s
    await s.close()


async def test_token_bucket_allows_capacity_then_rejects(store):
    k = key()
    decisions = [await store.take_token(k, FOO) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert decisions[-1].retry_after > 0


async def test_leaky_bucket_queues_then_rejects(store):
    k = key()
    decisions = [await store.schedule(k, BAR) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert decisions[1].wait == pytest.approx(2, abs=0.05)
    assert decisions[2].wait == pytest.approx(4, abs=0.05)


async def test_keys_are_independent(store):
    a, b = key(), key()
    for _ in range(3):
        await store.take_token(a, FOO)
    assert (await store.take_token(b, FOO)).allowed


async def test_burst_of_50_concurrent_requests_admits_exactly_capacity(store):
    for _ in range(20):
        k = key()
        decisions = await asyncio.gather(*(store.take_token(k, NO_REFILL) for _ in range(50)))
        assert sum(d.allowed for d in decisions) == NO_REFILL.capacity


async def test_burst_on_leaky_bucket_admits_exactly_one_plus_queue_depth(store):
    limit = LeakyBucketLimit(drain_per_second=0.01, queue_depth=4)
    for _ in range(20):
        k = key()
        decisions = await asyncio.gather(*(store.schedule(k, limit) for _ in range(50)))
        assert sum(d.allowed for d in decisions) == 1 + limit.queue_depth


@pytest.mark.redis
async def test_redis_keys_expire_once_idle(redis_url):
    client = aioredis.Redis.from_url(redis_url)
    s = RedisStore(client)
    k1, k2 = key(), key()
    await s.take_token(k1, FOO)
    await s.schedule(k2, BAR)
    assert 0 < await client.pttl(k1) <= 4_000  # 1 token missing at 0.5/s = 2 s, +1 s
    assert 0 < await client.pttl(k2) <= 3_000  # next_free in 2 s, +1 s
    await s.close()


@pytest.mark.redis
async def test_redis_store_uses_the_redis_clock_by_default(redis_url):
    # No clock injected: the Lua scripts must read redis TIME and still behave.
    s = RedisStore(aioredis.Redis.from_url(redis_url))
    assert s.uses_server_time
    k = key()
    assert [(await s.take_token(k, FOO)).allowed for _ in range(4)] == [True, True, True, False]
    await s.close()
