"""Startup configuration: client limits from YAML, and which store to use."""
import math
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import redis
import yaml
from redis.asyncio import Redis

from app.algorithms import LeakyBucketLimit, TokenBucketLimit
from app.stores import MemoryStore, RedisStore


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ClientLimits:
    foo: TokenBucketLimit
    bar: LeakyBucketLimit


def load_clients(path: str | Path) -> dict[str, ClientLimits]:
    raw = yaml.safe_load(Path(path).read_text())
    clients = raw.get("clients") if isinstance(raw, dict) else None
    if not isinstance(clients, dict) or not clients:
        raise ConfigError(f"{path}: expected a non-empty 'clients' mapping")

    result = {}
    for client_id, limits in clients.items():
        where = f"clients.{client_id}"
        foo = _section(limits, where, "foo")
        bar = _section(limits, where, "bar")
        result[str(client_id)] = ClientLimits(
            foo=TokenBucketLimit(
                capacity=_number(foo, f"{where}.foo", "capacity", integer=True, minimum=1),
                refill_per_second=_number(foo, f"{where}.foo", "refill_per_second"),
            ),
            bar=LeakyBucketLimit(
                drain_per_second=_number(bar, f"{where}.bar", "drain_per_second"),
                queue_depth=_number(bar, f"{where}.bar", "queue_depth", integer=True, minimum=0),
            ),
        )
    return result


def _section(limits, where, name):
    section = limits.get(name) if isinstance(limits, dict) else None
    if not isinstance(section, dict):
        raise ConfigError(f"{where}.{name}: missing")
    return section


def _number(section, where, name, integer=False, minimum=None):
    value = section.get(name)
    if integer:
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= minimum
        if not ok:
            raise ConfigError(f"{where}.{name}: must be an integer >= {minimum}, got {value!r}")
    else:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < math.inf
        if not ok:
            raise ConfigError(f"{where}.{name}: must be a finite number > 0, got {value!r}")
    return value


# Without timeouts a Redis that stops answering would hang requests instead of
# failing closed with a 503.
REDIS_TIMEOUTS = {"socket_connect_timeout": 2, "socket_timeout": 2}


def redis_client(url: str) -> Redis:
    return Redis.from_url(url, **REDIS_TIMEOUTS)


def store_from_env(env) -> MemoryStore | RedisStore:
    """STORAGE=memory (default) or redis. With redis, fail now if Redis can't be reached."""
    storage = env.get("STORAGE", "memory")
    if storage == "memory":
        return MemoryStore()
    if storage == "redis":
        # 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and
        # waits 2 s per connection before falling back to the IPv4-only local Redis.
        url = env.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        try:
            with redis.Redis.from_url(url, **REDIS_TIMEOUTS) as client:
                client.ping()
        except redis.exceptions.RedisError as e:
            # Name where we tried to connect, never the password.
            where = urlsplit(url)
            target = f"{where.scheme}://{where.hostname}:{where.port or 6379}"
            hint = " Hosted Redis (e.g. Upstash) needs TLS: use rediss://." if where.scheme == "redis" else ""
            raise ConfigError(f"Redis not reachable at {target} ({e}).{hint}") from e
        use_server_time = env.get("REDIS_USE_SERVER_TIME", "true").lower() != "false"
        return RedisStore(redis_client(url), clock=None if use_server_time else time.time)
    raise ConfigError(f"STORAGE must be 'memory' or 'redis', got {storage!r}")
