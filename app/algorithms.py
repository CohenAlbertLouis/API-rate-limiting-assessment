"""Rate-limiting algorithms as pure functions.

Each function takes the stored state (or None for a client never seen), the
current time in seconds, and the client's limit, and returns the decision plus
the new state to store. No I/O and no clock here, so both stores (memory and
Redis) share one definition of "correct", and tests can control time.
"""
from dataclasses import dataclass


EPSILON = 1e-9  # absorbs float rounding, e.g. 4/3 s * 0.75/s = 0.9999…


@dataclass(frozen=True)
class Decision:
    allowed: bool
    wait: float = 0.0  # seconds the request must be held before answering (leaky bucket)
    retry_after: float = 0.0  # seconds until a retry could succeed (when rejected)


# --- Token bucket (/foo) ------------------------------------------------------


@dataclass(frozen=True)
class TokenBucketLimit:
    capacity: int  # burst size
    refill_per_second: float


@dataclass(frozen=True)
class TokenBucketState:
    tokens: float
    updated_at: float


def token_bucket(
    state: TokenBucketState | None, now: float, limit: TokenBucketLimit
) -> tuple[Decision, TokenBucketState]:
    if state is None:
        state = TokenBucketState(tokens=limit.capacity, updated_at=now)

    # A clock going backwards adds nothing, and never rewinds the stored time
    # (otherwise the same seconds would be counted twice on the next request).
    now = max(now, state.updated_at)
    tokens = min(limit.capacity, state.tokens + (now - state.updated_at) * limit.refill_per_second)

    if tokens >= 1 - EPSILON:
        return Decision(allowed=True), TokenBucketState(max(0.0, tokens - 1), now)

    retry_after = (1 - tokens) / limit.refill_per_second
    return Decision(allowed=False, retry_after=retry_after), TokenBucketState(tokens, now)


# --- Leaky bucket with a queue (/bar) -----------------------------------------
#
# Requests leave the bucket at a fixed rate. Instead of keeping a real queue we
# store one number per client: `next_free`, the time the next request may leave.
# An arriving request takes the slot max(now, next_free) and waits until then.
# If that wait is longer than a full queue would take to drain, it is rejected.


@dataclass(frozen=True)
class LeakyBucketLimit:
    drain_per_second: float
    queue_depth: int  # requests allowed to wait; 0 = reject anything that can't go now

    @property
    def interval(self) -> float:
        return 1 / self.drain_per_second

    @property
    def max_wait(self) -> float:
        return self.queue_depth * self.interval


def leaky_bucket(
    next_free: float | None, now: float, limit: LeakyBucketLimit
) -> tuple[Decision, float]:
    slot = now if next_free is None else max(now, next_free)
    wait = slot - now

    if wait <= limit.max_wait + EPSILON:
        return Decision(allowed=True, wait=wait), slot + limit.interval

    retry_after = wait - limit.max_wait
    return Decision(allowed=False, retry_after=retry_after), next_free
