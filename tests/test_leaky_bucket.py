"""R5 — /bar leaky bucket with a bounded queue (spec § Algorithms)."""
from hypothesis import given, strategies as st

from app.algorithms import LeakyBucketLimit, leaky_bucket

LIMIT = LeakyBucketLimit(drain_per_second=0.5, queue_depth=2)  # client-1: 1 req / 2 s, queue 2


def run(times, limit=LIMIT):
    """Feed request times through the algorithm; return decisions."""
    next_free, decisions = None, []
    for now in times:
        decision, next_free = leaky_bucket(next_free, now, limit)
        decisions.append(decision)
    return decisions


def test_five_concurrent_requests_one_immediate_two_queued_two_rejected():
    decisions = run([0, 0, 0, 0, 0])
    assert [d.allowed for d in decisions] == [True, True, True, False, False]
    assert [d.wait for d in decisions[:3]] == [0, 2, 4]


def test_client_2_eight_concurrent_one_immediate_four_queued_three_rejected():
    limit = LeakyBucketLimit(drain_per_second=1, queue_depth=4)
    decisions = run([0] * 8, limit)
    assert [d.wait for d in decisions if d.allowed] == [0, 1, 2, 3, 4]
    assert sum(not d.allowed for d in decisions) == 3


def test_sequential_client_is_slowed_down_never_rejected():
    # each request arrives the moment the previous one was answered
    t, decisions = 0.0, []
    next_free = None
    for _ in range(10):
        d, next_free = leaky_bucket(next_free, t, LIMIT)
        decisions.append(d)
        t += d.wait
    assert all(d.allowed for d in decisions)


def test_queue_depth_zero_is_a_strict_meter():
    limit = LeakyBucketLimit(drain_per_second=1, queue_depth=0)
    decisions = run([0, 0, 0.5, 1.0], limit)
    assert [d.allowed for d in decisions] == [True, False, False, True]


def test_rejection_retry_after_is_time_until_queue_has_room():
    decisions = run([0, 0, 0, 0])
    assert decisions[-1].retry_after == 2  # would wait 6 s, max wait is 4 s


def test_bucket_drains_while_idle():
    decisions = run([0, 0, 0, 10])
    assert decisions[-1].allowed and decisions[-1].wait == 0


# --- properties -------------------------------------------------------------

limits = st.builds(
    LeakyBucketLimit,
    drain_per_second=st.floats(0.1, 10),
    queue_depth=st.integers(0, 6),
)
gaps = st.lists(st.floats(0, 3), min_size=1, max_size=60)


def timeline(gap_list):
    t, out = 0.0, []
    for g in gap_list:
        t += g
        out.append(t)
    return out


def interval(limit):
    return 1 / limit.drain_per_second


@given(limits, gaps)
def test_releases_are_spaced_at_least_one_interval_apart(limit, gap_list):
    times = timeline(gap_list)
    releases = [t + d.wait for t, d in zip(times, run(times, limit)) if d.allowed]
    assert all(b - a >= interval(limit) - 1e-6 for a, b in zip(releases, releases[1:]))


@given(limits, gaps)
def test_no_request_waits_longer_than_the_queue_allows(limit, gap_list):
    decisions = run(timeline(gap_list), limit)
    assert all(0 <= d.wait <= limit.queue_depth * interval(limit) + 1e-6 for d in decisions)


@given(limits, gaps)
def test_rejects_only_when_the_queue_is_full(limit, gap_list):
    times = timeline(gap_list)
    decisions = run(times, limit)
    releases = []
    for now, d in zip(times, decisions):
        if d.allowed:
            releases.append(now + d.wait)
        else:
            # requests still in the bucket: released within the last interval or later
            pending = sum(r > now - interval(limit) + 1e-6 for r in releases)
            assert pending >= limit.queue_depth + 1


@given(limits, st.lists(st.floats(0, 3), min_size=1, max_size=60))
def test_requests_spaced_one_interval_apart_never_wait(limit, extra):
    decisions = run(timeline([interval(limit) + e for e in extra]), limit)
    assert all(d.allowed and d.wait < 1e-6 for d in decisions)
