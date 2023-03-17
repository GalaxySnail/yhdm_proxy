# Copied from h11/examples/trio-server.py
# https://github.com/python-hyper/h11/blob/v0.13.0/examples/trio-server.py
# LICENSE: MIT

from __future__ import annotations

import sys
import time
import pprint
import datetime
import traceback
import email.utils
from itertools import count
from dataclasses import dataclass, field
from collections.abc import Iterator, Iterable

import typing
from typing import ClassVar, Any, TypeAlias

import trio
# httpcore pinned h11<0.13.0, we need to wait for it
import h11  # type: ignore

from ._version import __version__
from . import _log
from ._context import request


# 在 python3.10 之前，不要在运行时执行如下代码
if typing.TYPE_CHECKING or sys.version_info >= (3, 10):
    BytesLikeStr: TypeAlias = bytes | bytearray | str
    H11Headers: TypeAlias = Iterable[tuple[BytesLikeStr, BytesLikeStr]]


# We are using email.utils.format_datetime to generate the Date header.
# It may sound weird, but it actually follows the RFC.
# Please see: https://stackoverflow.com/a/59416334/14723771
#
# See also:
# [1] https://www.rfc-editor.org/rfc/rfc7231#section-7.1.1.1
# [2] https://www.rfc-editor.org/rfc/rfc5322#section-3.3
def format_date_time(dt: datetime.datetime | None = None):
    """Generate a RFC 7231 IMF-fixdate string"""
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone.utc)
    return email.utils.format_datetime(dt, usegmt=True)


# FIXME 这个类包含了太多逻辑，考虑用 ContextVar 重构
@dataclass
class TrioHTTPWrapper(trio.abc.AsyncResource):
    stream: trio.SocketStream
    logger: _log.Logger
    max_recv: int = 64 * 1024
    timeout: int = 10
    conn: h11.Connection = field(init=False)
    ident: bytes = field(init=False)

    _next_id: ClassVar[Iterator[int]] = count()

    def __post_init__(self):
        self.conn = h11.Connection(h11.SERVER)
        # Our Server: header
        self.ident = " ".join(
            [f"yhdm_proxy/{__version__}", h11.PRODUCT_ID]
        ).encode("ascii")
        # A unique id for this connection, to include in debugging output
        # (useful for understanding what's going on if there are multiple
        # simultaneous clients).
        self._obj_id = next(TrioHTTPWrapper._next_id)

    async def _send(self, event):
        # The code below doesn't send ConnectionClosed, so we don't bother
        # handling it here either -- it would require that we do something
        # appropriate when 'data' is None.
        if isinstance(event, h11.ConnectionClosed):
            raise RuntimeError("can't send anything when connection is closed")
        data = self.conn.send(event)
        try:
            await self.stream.send_all(data)
        except BaseException:
            # 如果发送失败，则 stream 的状态不可预知，只能关闭
            self.conn.send_failed()
            raise

    async def send_response(
        self, *,
        status_code: int,
        headers: H11Headers,
        reason: bytes | str = b"",
    ) -> None:
        res = h11.Response(
            status_code=status_code,
            headers=headers,
            reason=reason,
        )
        # await self.log(f"send response: {res}")
        await self._send(res)

    async def send_data(self, data: bytes | bytearray | memoryview) -> None:
        if request.get().method != b"HEAD":
            # 只有在不是 HEAD 请求的时候发送响应体
            await self._send(h11.Data(data=data))

    async def send_eof(self) -> None:
        await self._send(h11.EndOfMessage())

    async def _read_from_peer(self):
        if self.conn.they_are_waiting_for_100_continue:
            go_ahead = h11.InformationalResponse(
                status_code=100, headers=self._basic_headers()
            )
            await self._send(go_ahead)
        try:
            data = await self.stream.receive_some(self.max_recv)
        except ConnectionError:
            # They've stopped listening. Not much we can do about it here.
            data = b""
        self.conn.receive_data(data)

    async def next_event(self):
        while True:
            event = self.conn.next_event()
            if event is h11.NEED_DATA:
                await self._read_from_peer()
                continue
            return event

    async def _shutdown_and_clean_up(self):
        # When this method is called, it's because we definitely want to kill
        # this connection, either as a clean shutdown or because of some kind
        # of error or loss-of-sync bug, and we no longer care if that violates
        # the protocol or not. So we ignore the state of self.conn, and just
        # go ahead and do the shutdown on the socket directly. (If you're
        # implementing a client you might prefer to send ConnectionClosed()
        # and let it raise an exception if that violates the protocol.)
        #
        try:
            await self.stream.send_eof()
        except trio.BrokenResourceError:
            # They're already gone, nothing to do
            return
        # Wait and read for a bit to give them a chance to see that we closed
        # things, but eventually give up and just close the socket.
        # XX FIXME: possibly we should set SO_LINGER to 0 here, so
        # that in the case where the client has ignored our shutdown and
        # declined to initiate the close themselves, we do a violent shutdown
        # (RST) and avoid the TIME_WAIT?
        # it looks like nginx never does this for keepalive timeouts, and only
        # does it for regular timeouts (slow clients I guess?) if explicitly
        # enabled ("Default: reset_timedout_connection off")
        with trio.move_on_after(self.timeout):
            try:
                while True:
                    # Attempt to read until EOF
                    got = await self.stream.receive_some(self.max_recv)
                    if not got:
                        break
            except trio.BrokenResourceError:
                pass
            finally:
                await self.stream.aclose()

    async def aclose(self):
        await self._shutdown_and_clean_up()

    def _basic_headers(self) -> list[tuple[BytesLikeStr, BytesLikeStr]]:
        # HTTP requires these headers in all responses (client would do
        # something different here)
        return [
            ("Date", format_date_time().encode("ascii")),
            ("Server", self.ident),
        ]

    def simple_response_header(
        self,
        content_type: BytesLikeStr | None,
        content_length: int,
    ) -> list[tuple[BytesLikeStr, BytesLikeStr]]:
        headers: list[tuple[BytesLikeStr, BytesLikeStr]]
        headers = self._basic_headers()
        if content_type is not None:
            headers.append(("Content-Type", content_type))
        headers.append(("Content-Length", str(content_length)))
        return headers

    async def log(self, msg: str) -> None:
        await self.logger.log(f"{self._obj_id}: {msg}")

    async def log_exception(self, msg: str, exc: Exception | None = None) -> None:
        exc_info_str = (
            traceback.format_exc()
            if exc is None else
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        await self.log(
            f"{msg}\n"
            "---- start exception info -----\n"
            f"{exc_info_str}"
            "---- end exception info -----"
        )

    async def log_pprint(self, msg: str, name: str, value: Any) -> None:
        await self.log(
            f"{msg}\n"
            f"----- start {name} -----\n"
            f"{pprint.pformat(value)}\n"
            f"----- end {name} -----"
        )
