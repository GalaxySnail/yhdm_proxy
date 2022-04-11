from __future__ import annotations

import traceback
from functools import partial
from collections.abc import Callable, Awaitable

import trio

from . import _log
from ._handler import http_handler, CloseConnection
from ._trio_http_server import TrioHTTPWrapper


async def handler(
    stream: trio.SocketStream,
    get_logger: Callable[[], Awaitable[_log.Logger]],
) -> None:
    async with \
            stream, \
            await get_logger() as logger, \
            TrioHTTPWrapper(stream, logger) as http_wrapper:

        remote_addr, remote_port, *_ = stream.socket.getpeername()
        local_addr, local_port, *_ = stream.socket.getsockname()
        await http_wrapper.log(
            f"get a connection from {remote_addr} port {remote_port} "
            f"on {local_addr} port {local_port}"
        )
        try:
            await http_handler(http_wrapper)
        except CloseConnection:
            pass
        except Exception:  # pylint: disable=W0703
            await http_wrapper.log(
                "Uncatched exception:\n"
                "----- start exception info -----\n"
                f"{traceback.format_exc()}"
                "----- end exception info -----"
            )
        finally:
            await http_wrapper.log("Closed.")


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
