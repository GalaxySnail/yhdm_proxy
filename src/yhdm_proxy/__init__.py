from __future__ import annotations

from functools import partial
from collections.abc import Callable, Awaitable

import trio
# httpcore pinned h11<0.13.0, we need to wait for it
import h11  # type: ignore
import httpx

from . import _log


async def handler(
    stream: trio.SocketStream,
    get_logger: Callable[[], Awaitable[_log.Logger]],
) -> None:
    pass


async def serve(host: str = "localhost", port: int = 0) -> None:
    nursery: trio.Nursery
    async with trio.open_nursery() as nursery:
        get_logger: Callable[[], Awaitable[_log.Logger]]
        get_logger = await nursery.start(_log.log_task)

        listeners: list[trio.SocketListener]
        listeners = await nursery.start(partial(
            trio.serve_tcp,  # type: ignore[arg-type]
            partial(handler, get_logger=get_logger),
            port,
            host=host,
        ))

        async with await get_logger() as logger:
            for listener in listeners:
                addr, port, *_ = listener.socket.getsockname()
                await logger.log(f"Serve HTTP on {addr}, port {port}")
