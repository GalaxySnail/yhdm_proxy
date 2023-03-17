from __future__ import annotations

from functools import partial
from collections.abc import Callable, Awaitable

import trio

from ._version import __version__
from . import _log
from ._handler import http_handler
from ._exceptions import CloseConnection
from ._trio_http_server import TrioHTTPWrapper
from ._task_resources import open_exit_stack


async def handler(
    stream: trio.SocketStream,
    get_logger: Callable[[], Awaitable[_log.Logger]],
) -> None:
    http_wrapper: TrioHTTPWrapper
    async with \
            stream, \
            await get_logger() as logger, \
            TrioHTTPWrapper(stream, logger) as http_wrapper, \
            open_exit_stack():

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
            await http_wrapper.log_exception("Uncatched exception:")
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
