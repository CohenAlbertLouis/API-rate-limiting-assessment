"""R14 - the Lua scripts make exactly the decisions the Python functions make."""
import uuid

import pytest
import redis
from hypothesis import HealthCheck, given, settings, strategies as st

from app.algorithms import LeakyBucketLimit, TokenBucketLimit, leaky_bucket, token_bucket
from app.stores import LEAKY_BUCKET_LUA, TOKEN_BUCKET_LUA, parse_decision

pytestmark = pytest.mark.redis

gaps = st.lists(st.floats(0, 3), min_size=1, max_size=30)
token_limits = st.builds(TokenBucketLimit, capacity=st.integers(1, 10), refill_per_second=st.floats(0.1, 10))
leaky_limits = st.builds(LeakyBucketLimit, drain_per_second=st.floats(0.1, 10), queue_depth=st.integers(0, 6))
ORACLE = settings(max_examples=1000, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


def timeline(gap_list):
    t, out = 1_000.0, []
    for g in gap_list:
        t += g
        out.append(t)
    return out


def same(a, b):
    return a.allowed == b.allowed and a.wait == pytest.approx(b.wait, abs=1e-6) and a.retry_after == pytest.approx(
        b.retry_after, abs=1e-6
    )


@pytest.fixture(scope="module")
def r(redis_url):
    return redis.Redis.from_url(redis_url)


@ORACLE
@given(limit=token_limits, gap_list=gaps)
def test_token_bucket_lua_matches_python(r, limit, gap_list):
    script, k, state = r.register_script(TOKEN_BUCKET_LUA), f"oracle:{uuid.uuid4()}", None
    for now in timeline(gap_list):
        expected, state = token_bucket(state, now, limit)
        actual = parse_decision(script(keys=[k], args=[limit.capacity, limit.refill_per_second, repr(now)]))
        assert same(actual, expected), (now, actual, expected)


@ORACLE
@given(limit=leaky_limits, gap_list=gaps)
def test_leaky_bucket_lua_matches_python(r, limit, gap_list):
    script, k, state = r.register_script(LEAKY_BUCKET_LUA), f"oracle:{uuid.uuid4()}", None
    for now in timeline(gap_list):
        expected, state = leaky_bucket(state, now, limit)
        actual = parse_decision(script(keys=[k], args=[limit.drain_per_second, limit.queue_depth, repr(now)]))
        assert same(actual, expected), (now, actual, expected)
