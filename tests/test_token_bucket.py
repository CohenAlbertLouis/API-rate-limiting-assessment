"""R4 — /foo token bucket (spec § Algorithms)."""
from hypothesis import given, strategies as st

from app.algorithms import TokenBucketLimit, token_bucket

LIMIT = TokenBucketLimit(capacity=3, refill_per_second=0.5)


def run(times, limit=LIMIT):
    """Feed request times through the algorithm; return (decisions, states)."""
    state, decisions, states = None, [], []
    for now in times:
        decision, state = token_bucket(state, now, limit)
        decisions.append(decision)
        states.append(state)
    return decisions, states


def test_fresh_bucket_allows_a_burst_of_capacity_then_rejects():
    decisions, _ = run([0, 0, 0, 0])
    assert [d.allowed for d in decisions] == [True, True, True, False]


def test_one_token_comes_back_after_one_refill_period():
    decisions, _ = run([0, 0, 0, 1.9, 2.0])
    assert [d.allowed for d in decisions] == [True, True, True, False, True]


def test_retry_after_is_time_until_next_token():
    decisions, _ = run([0, 0, 0, 0.5])
    assert decisions[-1].retry_after == 1.5  # 0.25 tokens refilled, 0.75 missing at 0.5/s


def test_clock_going_backwards_adds_no_tokens():
    decisions, _ = run([10, 10, 10, 5, 10])
    assert [d.allowed for d in decisions] == [True, True, True, False, False]


def test_clock_going_backwards_removes_no_tokens():
    # Without the guard a backwards clock step would drain tokens.
    decisions, _ = run([10, 5])
    assert [d.allowed for d in decisions] == [True, True]


def test_allowed_requests_never_wait():
    decisions, _ = run([0, 0, 0])
    assert all(d.wait == 0 for d in decisions)


# --- properties -------------------------------------------------------------

limits = st.builds(
    TokenBucketLimit,
    capacity=st.integers(1, 10),
    refill_per_second=st.floats(0.1, 10),
)
gaps = st.lists(st.floats(0, 3), min_size=1, max_size=60)


def timeline(gap_list):
    t, out = 0.0, []
    for g in gap_list:
        t += g
        out.append(t)
    return out


@given(limits, gaps)
def test_tokens_stay_between_zero_and_capacity(limit, gap_list):
    _, states = run(timeline(gap_list), limit)
    assert all(0 <= s.tokens <= limit.capacity for s in states)


@given(limits, gaps)
def test_never_admits_more_than_capacity_plus_refill(limit, gap_list):
    times = timeline(gap_list)
    decisions, _ = run(times, limit)
    admitted = sum(d.allowed for d in decisions)
    assert admitted <= limit.capacity + limit.refill_per_second * (times[-1] - times[0]) + 1e-6


@given(limits, st.lists(st.floats(0, 3), min_size=1, max_size=60))
def test_requests_spaced_one_token_apart_are_always_allowed(limit, extra):
    period = 1 / limit.refill_per_second
    decisions, _ = run(timeline([period + e for e in extra]), limit)
    assert all(d.allowed for d in decisions)


@given(limits, gaps)
def test_a_rejection_reports_a_positive_retry_after(limit, gap_list):
    decisions, _ = run(timeline(gap_list), limit)
    assert all(d.retry_after > 0 for d in decisions if not d.allowed)


def test_pinned_float_rounding_does_not_reject_a_client_exactly_at_the_rate():
    # Hypothesis counterexample: 4/3 s * 0.75/s summed to 0.9999… tokens.
    limit = TokenBucketLimit(capacity=1, refill_per_second=0.75)
    period = 1 / 0.75
    decisions, _ = run(timeline([period] * 4), limit)
    assert all(d.allowed for d in decisions)
