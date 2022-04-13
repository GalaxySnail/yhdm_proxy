"""
一些用于读取字节流的帮助函数

所有接受 partial_result 参数的函数都遵循如下约定：
 - 传入的 PartialResult 必须为空，即 data 属性必须为 None
 - 如果函数有返回类型（不为None），则 PartialResult 的泛型参数应该与返回类型一致
 - 如果函数正常返回，则 partial_result 必须为空
 - 如果函数引发任何异常，则 partial_result 应该包含当前的不完整结果
"""
from __future__ import annotations

import sys
from functools import partial
from dataclasses import dataclass
from collections.abc import Callable, Awaitable, AsyncIterator

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

    def assert_empty(self) -> None:
        if self.data is not None:
            raise ValueError("partial_result must be empty")


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
    partial_result.assert_empty()

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
    partial_result.assert_empty()

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
    partial_result.assert_empty()

    buf = bytearray()
    async for data in read_iter:
        buf.extend(data)
        if len(buf) >= min_bytes:
            break
    else:
        partial_result.data = buf
        raise EOFError
    return buf


async def find_in_stream(
    read_iter: AsyncIterator[bytes | bytearray],
    sub: bytes | bytearray,
) -> AsyncIterator[tuple[bytes | bytearray, bool]]:
    """在字节流中查找子字节串 sub
    迭代元组第二项为 True 时，第一项一定以 sub 开头，
    即保证 .startswith(sub) 为真
    """
    buf = bytearray()

    async for data in read_iter:
        buf.extend(data)
        index = buf.find(sub)

        if index == 0:
            yield buf, True
            break
        elif index != -1:
            yield buf[:index], False
            yield buf[index:], True
            break

        # XXX 这里的切片存在较多复制开销，可以考虑改为 memoryview
        offset = -len(sub) + 1
        yield buf[:offset], False
        buf = buf[offset:]
