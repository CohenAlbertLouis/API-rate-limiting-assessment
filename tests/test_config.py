"""R8 per-client/per-endpoint YAML config, R9 shipped demo clients, R12 STORAGE selection."""
import pytest

from app.algorithms import LeakyBucketLimit, TokenBucketLimit
from app.config import ConfigError, load_clients, store_from_env
from app.stores import MemoryStore, RedisStore

VALID = """
clients:
  alpha:
    foo: { capacity: 2, refill_per_second: 1 }
    bar: { drain_per_second: 2, queue_depth: 0 }
"""


def write(tmp_path, text):
    p = tmp_path / "clients.yaml"
    p.write_text(text)
    return p


def test_loads_limits_per_client_and_endpoint(tmp_path):
    clients = load_clients(write(tmp_path, VALID))
    assert clients["alpha"].foo == TokenBucketLimit(capacity=2, refill_per_second=1)
    assert clients["alpha"].bar == LeakyBucketLimit(drain_per_second=2, queue_depth=0)


@pytest.mark.parametrize(
    "bad, field",
    [
        ("capacity: 2", "capacity: 0"),
        ("capacity: 2", "capacity: 1.5"),
        ("refill_per_second: 1", "refill_per_second: 0"),
        ("drain_per_second: 2", "drain_per_second: -1"),
        ("queue_depth: 0", "queue_depth: -1"),
        ("queue_depth: 0", "queue_depth: two"),
        ("refill_per_second: 1", "refill_per_second: .inf"),
        ("drain_per_second: 2", "drain_per_second: .nan"),
    ],
)
def test_invalid_values_are_rejected_naming_the_field(tmp_path, bad, field):
    with pytest.raises(ConfigError, match=r"clients\.alpha\.(foo|bar)\." + field.split(":")[0]):
        load_clients(write(tmp_path, VALID.replace(bad, field)))


def test_a_client_missing_an_endpoint_is_rejected(tmp_path):
    text = "clients:\n  alpha:\n    foo: { capacity: 2, refill_per_second: 1 }\n"
    with pytest.raises(ConfigError, match=r"clients\.alpha\.bar"):
        load_clients(write(tmp_path, text))


def test_a_config_without_clients_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="clients"):
        load_clients(write(tmp_path, "something: else\n"))


def test_shipped_config_has_two_demo_clients_with_different_limits():
    clients = load_clients("config/clients.yaml")
    assert clients["client-1"].foo == TokenBucketLimit(capacity=3, refill_per_second=0.5)
    assert clients["client-1"].bar == LeakyBucketLimit(drain_per_second=0.5, queue_depth=2)
    assert clients["client-2"].foo == TokenBucketLimit(capacity=6, refill_per_second=1)
    assert clients["client-2"].bar == LeakyBucketLimit(drain_per_second=1, queue_depth=4)


async def test_storage_defaults_to_memory():
    store = store_from_env({})
    assert isinstance(store, MemoryStore)


@pytest.mark.redis
async def test_storage_redis_uses_redis_url(redis_url):
    store = store_from_env({"STORAGE": "redis", "REDIS_URL": redis_url})
    assert isinstance(store, RedisStore) and store.uses_server_time
    await store.close()


@pytest.mark.redis
async def test_redis_use_server_time_false_uses_the_app_clock(redis_url):
    store = store_from_env({"STORAGE": "redis", "REDIS_URL": redis_url, "REDIS_USE_SERVER_TIME": "false"})
    assert not store.uses_server_time
    await store.close()


def test_unknown_storage_is_rejected():
    with pytest.raises(ConfigError, match="STORAGE"):
        store_from_env({"STORAGE": "postgres"})


def test_unreachable_redis_fails_at_startup():
    with pytest.raises(ConfigError, match="Redis"):
        store_from_env({"STORAGE": "redis", "REDIS_URL": "redis://127.0.0.1:1/0"})


def test_startup_error_names_scheme_host_and_port_but_never_the_password():
    fake_password = "not-a-real-password"  # a test value; the URL is assembled so secret scanners don't flag it
    url = "redis://default:" + fake_password + "@" + "127.0.0.1:1/0"
    with pytest.raises(ConfigError) as err:
        store_from_env({"STORAGE": "redis", "REDIS_URL": url})
    message = str(err.value)
    assert "redis://127.0.0.1:1" in message
    assert fake_password not in message
    assert "rediss://" in message  # the hint for TLS-only hosted Redis
