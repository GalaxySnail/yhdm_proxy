# Copied from h11/examples/trio-server.py
# https://github.com/python-hyper/h11/blob/v0.13.0/examples/trio-server.py
# LICENSE: MIT

import time
from itertools import count
from dataclasses import dataclass, field
from collections.abc import Iterator

from typing import ClassVar, Any

import trio
import h11  # type: ignore

from .__version__ import __version__
from . import _log


# Weekday and month names for HTTP date/time formatting; always English!
_weekdayname = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_monthname = [None, # Dummy so we can use 1-based month numbers
              "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

def format_date_time(timestamp):
    """stdlib wsgiref.handlers.format_date_time, but is not documented"""
    year, month, day, hh, mm, ss, wd, *_ = time.gmtime(timestamp)
    return (
        f"{_weekdayname[wd]}, "
        f"{day:02d} {_monthname[month]:3s} {year:4d} "
        f"{hh:02d}:{mm:02d}:{ss:02d} GMT"
    )


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

    async def send(self, event):
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

    async def _read_from_peer(self):
        if self.conn.they_are_waiting_for_100_continue:
            go_ahead = h11.InformationalResponse(
                status_code=100, headers=self.basic_headers()
            )
            await self.send(go_ahead)
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

    def basic_headers(self) -> list[tuple[str, Any]]:
        # HTTP requires these headers in all responses (client would do
        # something different here)
        return [
            ("Date", format_date_time(None).encode("ascii")),
            ("Server", self.ident),
        ]

    async def log(self, msg: str) -> None:
        await self.logger.log(f"{self._obj_id}: {msg}")
