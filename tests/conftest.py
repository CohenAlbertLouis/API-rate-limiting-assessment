import os

import pytest
import redis

# Tests use db 15 and random keys, so they never touch data the app is using.
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")


@pytest.fixture(scope="session")
def redis_url():
    # Skip only when Redis isn't there (refused, or timed out: a stopped port can
    # time out on Windows). A reachable but misconfigured Redis (bad password, db
    # that doesn't exist) must fail loudly, not skip every Redis test.
    try:
        with redis.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=1) as client:
            client.ping()
    except redis.exceptions.AuthenticationError:
        raise  # a subclass of ConnectionError in redis-py, so it must be let through first
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
        pytest.skip(f"Redis not reachable at {TEST_REDIS_URL} - run: docker compose up -d redis")
    return TEST_REDIS_URL
