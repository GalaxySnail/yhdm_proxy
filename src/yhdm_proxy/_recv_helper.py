from __future__ import annotations

import sys
from functools import partial
from dataclasses import dataclass
from collections.abc import ByteString, Callable, Awaitable, AsyncIterator

import typing
from typing import TypeVar, Generic

if sys.version_info >= (3, 8):
    from typing import Protocol, runtime_checkable
else:
    from typing_extensions import Protocol, runtime_checkable

if typing.TYPE_CHECKING:
    from _typeshed import WriteableBuffer

from ._utils import anext


T = TypeVar("T")


@runtime_checkable
class SupportsReceiveSome(Protocol):
    async def reveive_some(self, max_bytes: int) -> bytes | bytearray: ...


@runtime_checkable
class SupportsReceiveSomeInto(Protocol):
    async def reveive_some_into(self, writable_buf: WriteableBuffer) -> int: ...


@dataclass
class PartialResult(Generic[T]):
    data: T | None = None

    def clear(self) -> None:
        self.data = None


async def wrap_reveive_into(
    stream: SupportsReceiveSome,
    writable_buf: memoryview,
) -> int:
    data = await stream.reveive_some(len(writable_buf))
    length = len(data)
    writable_buf[:length] = data
    return length


async def receive_exactly_into(
    stream: SupportsReceiveSome | SupportsReceiveSomeInto,
    writable_buf: WriteableBuffer,
    partial_result: PartialResult[int],
) -> None:
    if partial_result is not None and partial_result.data is not None:
        raise ValueError("partial_result must be empty")

    receive_into: Callable[[WriteableBuffer], Awaitable[int]]
    if isinstance(stream, SupportsReceiveSomeInto):
        receive_into = stream.reveive_some_into
    else:
        receive_into = partial(wrap_reveive_into, stream)

    with memoryview(writable_buf).cast("B") as writable_mv:
        received_all = 0
        max_bytes = len(writable_mv)
        while max_bytes - received_all:
            with writable_mv[received_all:] as mv:
                received = await receive_into(mv)
            if not received:
                raise EOFError
            received_all += received
            partial_result.data = received_all

    partial_result.clear()


async def receive_exactly(
    stream: SupportsReceiveSome | SupportsReceiveSomeInto,
    max_bytes: int,
    partial_result: PartialResult[bytes | bytearray],
) -> bytes | bytearray:
    buf = bytearray(max_bytes)
    partial_result_int: PartialResult[int] = PartialResult()

    try:
        await receive_exactly_into(stream, buf, partial_result_int)
    except BaseException:
        partial_result.data = buf[:partial_result_int.data]
        raise

    return buf


async def read_at_least(
    read_iter: AsyncIterator[bytes | bytearray],
    min_bytes: int,
    partial_result: PartialResult[bytes | bytearray],
) -> bytes | bytearray:
    """从一个产生字节对象的异步迭代器中，尽可能读取至少 max_bytes 个字节"""
    buf = bytearray()
    async for data in read_iter:
        buf.extend(data)
        if len(buf) >= min_bytes:
            break
    else:
        partial_result.data = buf
        raise EOFError
    return buf
