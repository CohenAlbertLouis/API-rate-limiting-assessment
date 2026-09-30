"""R19 - the demo script proves 200-then-429 for both clients on both endpoints."""
import asyncio

import httpx
import pytest
import redis

from app.algorithms import Decision, LeakyBucketLimit, TokenBucketLimit
from app.config import ClientLimits
from app.main import create_app
from app.stores import MemoryStore
from scripts.demo import run_demo

FAST = {
    "client-1": ClientLimits(TokenBucketLimit(3, 0.5), LeakyBucketLimit(drain_per_second=20, queue_depth=2)),
    "client-2": ClientLimits(TokenBucketLimit(6, 1), LeakyBucketLimit(drain_per_second=40, queue_depth=4)),
}


def api(store):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(FAST, store)), base_url="http://test")


async def test_demo_passes_every_check_against_a_fresh_server(capsys):
    async with api(MemoryStore()) as http:
        passed, total = await run_demo(http, FAST, wait=False)
    assert (passed, total) == (8, 8)
    out = capsys.readouterr().out
    assert "client-1  /bar" in out and "waited" in out and "429" in out


class NoFooLimit(MemoryStore):
    async def take_token(self, key, limit):
        return await super().take_token(key, TokenBucketLimit(10_000, 1))


class FooTooStrict(MemoryStore):
    async def take_token(self, key, limit):
        return await super().take_token(key, TokenBucketLimit(1, limit.refill_per_second))


class BarQueueTooLong(MemoryStore):
    async def schedule(self, key, limit):
        return await super().schedule(key, LeakyBucketLimit(limit.drain_per_second, limit.queue_depth + 10))


class BarNeverQueues(MemoryStore):
    """Admits the right number of /bar requests but never holds them."""

    async def schedule(self, key, limit):
        d = await super().schedule(key, limit)
        return Decision(allowed=d.allowed, retry_after=d.retry_after)


class FooFailsOnce(MemoryStore):
    """One /foo request hits a store error (503): the burst then holds too few 429s."""

    calls = 0

    async def take_token(self, key, limit):
        self.calls += 1
        if self.calls == 1:
            raise redis.exceptions.ConnectionError("blip")
        return await super().take_token(key, limit)


@pytest.mark.parametrize("broken", [NoFooLimit, FooTooStrict, FooFailsOnce, BarQueueTooLong, BarNeverQueues])
async def test_demo_fails_a_check_when_the_server_misbehaves(broken, capsys):
    async with api(broken()) as http:
        passed, total = await run_demo(http, FAST, wait=False)
    assert passed < total


class SlowStore(MemoryStore):
    """Every decision takes 0.3 s, like a remote server: tokens refill between requests."""

    async def take_token(self, key, limit):
        await asyncio.sleep(0.3)
        return await super().take_token(key, limit)


async def test_demo_passes_against_a_slow_remote_server(capsys):
    async with api(SlowStore()) as http:
        passed, total = await run_demo(http, FAST, wait=False)
    assert (passed, total) == (8, 8)
