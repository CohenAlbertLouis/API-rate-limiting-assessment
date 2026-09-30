"""R23 - a Redis that stops answering must fail closed within seconds, not hang."""
import asyncio

import pytest
import redis

from app.algorithms import TokenBucketLimit
from app.config import ConfigError, redis_client, store_from_env
from app.stores import RedisStore


@pytest.fixture
async def silent_redis_url():
    """A TCP server that accepts connections and never replies."""
    writers = []
    server = await asyncio.start_server(lambda r, w: writers.append(w), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"redis://127.0.0.1:{port}/0"
    for w in writers:
        w.close()
    server.close()


async def test_a_redis_that_stops_answering_raises_a_redis_error_quickly(silent_redis_url):
    store = RedisStore(redis_client(silent_redis_url))
    with pytest.raises(redis.exceptions.TimeoutError):
        await asyncio.wait_for(store.take_token("k", TokenBucketLimit(1, 1)), timeout=10)


async def test_startup_fails_quickly_when_redis_accepts_but_never_answers(silent_redis_url):
    env = {"STORAGE": "redis", "REDIS_URL": silent_redis_url}
    with pytest.raises(ConfigError):
        await asyncio.wait_for(asyncio.to_thread(store_from_env, env), timeout=10)
