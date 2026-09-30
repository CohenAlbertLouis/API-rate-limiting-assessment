"""Demo: both clients hit /foo and /bar until each limit overflows.

    python scripts/demo.py                                  # local server on :8000
    python scripts/demo.py --base-url https://<service-url>

Each endpoint gets a burst of concurrent requests (sent at once, so network
latency can't let buckets refill mid-burst):
/foo: `capacity` get 200, the other 2 get 429.
/bar: 1 goes straight through, `queue_depth` are held, the other 2 get 429.
(A client sending /bar requests one at a time is only slowed down, never rejected.)
"""
import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ClientLimits, load_clients  # noqa: E402


async def demo_foo(http, client_id, limits: ClientLimits) -> list[bool]:
    capacity = limits.foo.capacity
    # Sent all at once, so network latency can't refill tokens mid-burst.
    responses = await asyncio.gather(*(http.get("/foo", headers=auth(client_id)) for _ in range(capacity + 2)))
    codes = sorted(r.status_code for r in responses)
    for r in sorted(responses, key=lambda r: r.status_code):
        extra = f"retry-after {r.headers['retry-after']}s" if r.status_code == 429 else ""
        print(f"{client_id}  /foo  {r.status_code}  {extra}")
    return [codes.count(200) == capacity, codes.count(429) == 2]


async def demo_bar(http, client_id, limits: ClientLimits) -> list[bool]:
    queue_depth = limits.bar.queue_depth
    n = 1 + queue_depth + 2
    responses = await asyncio.gather(*(http.get("/bar", headers=auth(client_id)) for _ in range(n)))
    waits = sorted(int(r.headers["x-queue-wait-ms"]) / 1000 for r in responses if r.status_code == 200)
    for w in waits:
        print(f"{client_id}  /bar  200  waited {w:.1f}s")
    for r in responses:
        if r.status_code != 200:
            print(f"{client_id}  /bar  {r.status_code}  (queue full)")
    interval = 1 / limits.bar.drain_per_second
    released_one_interval_apart = all(b - a >= interval / 2 for a, b in zip(waits, waits[1:]))
    rest_rejected = len(waits) == 1 + queue_depth and sum(r.status_code == 429 for r in responses) == n - len(waits)
    return [released_one_interval_apart, rest_rejected]


async def run_demo(http, clients: dict[str, ClientLimits], wait=True) -> tuple[int, int]:
    if wait:
        # So a second run starts from full buckets: wait for the slowest one to recover.
        seconds = max(
            max(c.foo.capacity / c.foo.refill_per_second, (c.bar.queue_depth + 1) / c.bar.drain_per_second)
            for c in clients.values()
        )
        print(f"Waiting {seconds:.0f}s so every bucket starts full...\n")
        await asyncio.sleep(seconds)

    # Open the connections the bursts will reuse (unauthenticated -> 401, no
    # counters touched), so no burst includes connection or TLS setup time.
    # Against a sleeping Render service this is also the wake-up call.
    burst = max(max(c.foo.capacity, 1 + c.bar.queue_depth) + 2 for c in clients.values())
    print("Warming up connections (the 401s in the server log are these)...\n")
    await asyncio.gather(*(http.get("/foo") for _ in range(burst)))

    checks = []
    for client_id, limits in clients.items():
        checks += await demo_foo(http, client_id, limits)
        checks += await demo_bar(http, client_id, limits)
        print()
    print(f"{sum(checks)}/{len(checks)} checks passed")
    return sum(checks), len(checks)


def auth(client_id):
    return {"Authorization": f"Bearer {client_id}"}


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--config", default="config/clients.yaml")
    parser.add_argument("--no-wait", action="store_true", help="skip waiting for buckets to refill")
    args = parser.parse_args()
    # Keep idle connections open between bursts (httpx drops them after 5 s by default).
    limits = httpx.Limits(keepalive_expiry=60)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=90, limits=limits) as http:
        passed, total = await run_demo(http, load_clients(args.config), wait=not args.no_wait)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
