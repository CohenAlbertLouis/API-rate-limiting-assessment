"""The HTTP layer: authenticate, ask the store for a decision, answer."""
import asyncio
import logging
import math

import redis
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

from app.config import ClientLimits

log = logging.getLogger("rate_limit")


def create_app(clients: dict[str, ClientLimits], store) -> FastAPI:
    # Only /foo and /bar. Without the OpenAPI schema FastAPI also skips /docs and /redoc.
    app = FastAPI(title="Rate-limited API", openapi_url=None)

    @app.get("/foo")
    async def foo(authorization: str | None = Header(default=None)):
        return await handle("foo", authorization)

    @app.get("/bar")
    async def bar(authorization: str | None = Header(default=None)):
        return await handle("bar", authorization)

    async def handle(endpoint: str, authorization: str | None) -> JSONResponse:
        client_id = parse_client_id(authorization)
        if client_id not in clients:
            record("-", endpoint, "unauthorized")
            return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})

        key = f"rl:{endpoint}:{client_id}"
        try:
            if endpoint == "foo":
                decision = await store.take_token(key, clients[client_id].foo)
            else:
                decision = await store.schedule(key, clients[client_id].bar)
        except redis.exceptions.RedisError:
            # Fail closed: a limiter that quietly lets everything through is worse.
            log.exception(line(client_id, endpoint, "store_error"))
            return JSONResponse({"error": "rate limiter unavailable"}, 503)

        if not decision.allowed:
            record(client_id, endpoint, "rejected")
            retry_after = max(1, math.ceil(decision.retry_after))
            return JSONResponse({"error": "rate limit exceeded"}, 429, headers={"Retry-After": str(retry_after)})

        if endpoint == "foo":
            record(client_id, endpoint, "allowed")
            return JSONResponse({"success": True})

        # /bar: hold the request until its slot in the leaky bucket comes up.
        wait_ms = round(decision.wait * 1000)
        record(client_id, endpoint, "queued" if wait_ms else "allowed", wait_ms)
        await asyncio.sleep(decision.wait)
        return JSONResponse({"success": True}, headers={"X-Queue-Wait-Ms": str(wait_ms)})

    def record(client_id, endpoint, outcome, wait_ms=0):
        log.info(line(client_id, endpoint, outcome, wait_ms))

    def line(client_id, endpoint, outcome, wait_ms=0):
        return f"client={client_id} endpoint={endpoint} outcome={outcome} wait_ms={wait_ms} store={store.name}"

    return app


def parse_client_id(authorization: str | None) -> str | None:
    """'Bearer <client-id>' -> client-id. The scheme is case-insensitive."""
    scheme, _, client_id = (authorization or "").partition(" ")
    return client_id.strip() if scheme.lower() == "bearer" else None
