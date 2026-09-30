"""Where rate-limit state lives: in this process, or in Redis.

Both stores expose the same two methods, one per algorithm. Each call is one
atomic read-decide-write, so concurrent requests for the same client can never
both spend the same token or the same queue slot.
"""
import time
from pathlib import Path

from redis.asyncio import Redis

from app.algorithms import (
    Decision,
    LeakyBucketLimit,
    TokenBucketLimit,
    leaky_bucket,
    token_bucket,
)

LUA_DIR = Path(__file__).parent / "lua"
TOKEN_BUCKET_LUA = (LUA_DIR / "token_bucket.lua").read_text()
LEAKY_BUCKET_LUA = (LUA_DIR / "leaky_bucket.lua").read_text()


class MemoryStore:
    """State in a dict. Lost when the process restarts.

    No lock is needed: read, decide and write below contain no `await`, so the
    event loop cannot switch to another request halfway through. Adding an
    `await` between the read and the write would break that (a test covers it).
    """

    name = "memory"

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._state = {}

    async def take_token(self, key: str, limit: TokenBucketLimit) -> Decision:
        state = self._state.get(key)
        decision, self._state[key] = token_bucket(state, self._clock(), limit)
        return decision

    async def schedule(self, key: str, limit: LeakyBucketLimit) -> Decision:
        next_free = self._state.get(key)
        decision, self._state[key] = leaky_bucket(next_free, self._clock(), limit)
        return decision

    async def close(self) -> None:
        pass


class RedisStore:
    """State in Redis. Each decision is one Lua script, which Redis runs atomically.

    By default the scripts read Redis's own clock, so every app instance agrees
    on the time. Pass `clock` to supply the time from the app instead.
    """

    name = "redis"

    def __init__(self, redis: Redis, clock=None):
        self._redis = redis
        self._clock = clock
        # register_script sends EVALSHA and falls back to EVAL if Redis lost the script
        self._token_bucket = redis.register_script(TOKEN_BUCKET_LUA)
        self._leaky_bucket = redis.register_script(LEAKY_BUCKET_LUA)

    @property
    def uses_server_time(self) -> bool:
        return self._clock is None

    def _now(self) -> str:
        return "" if self._clock is None else repr(self._clock())

    async def take_token(self, key: str, limit: TokenBucketLimit) -> Decision:
        args = [limit.capacity, limit.refill_per_second, self._now()]
        return parse_decision(await self._token_bucket(keys=[key], args=args))

    async def schedule(self, key: str, limit: LeakyBucketLimit) -> Decision:
        args = [limit.drain_per_second, limit.queue_depth, self._now()]
        return parse_decision(await self._leaky_bucket(keys=[key], args=args))

    async def close(self) -> None:
        await self._redis.aclose()


def parse_decision(reply) -> Decision:
    """Lua returns {allowed, wait, retry_after}; floats come back as strings."""
    allowed, wait, retry_after = reply
    return Decision(allowed=bool(int(allowed)), wait=float(wait), retry_after=float(retry_after))
