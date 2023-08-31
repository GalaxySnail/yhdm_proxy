from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar

import h11
import trio
import httpx

from ._task_resources import register_async_resource


httpx_client: ContextVar[httpx.AsyncClient] = ContextVar("httpx_client")

request: ContextVar[h11.Request] = ContextVar("request")


@dataclass
class HTTPClientWrapper(trio.abc.AsyncResource):
    client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.client.aclose()


def get_client() -> httpx.AsyncClient:
    client = httpx_client.get(None)
    if client is not None:
        return client

    client = httpx.AsyncClient(follow_redirects=True)
    register_async_resource(HTTPClientWrapper(client))
    httpx_client.set(client)
    return client


