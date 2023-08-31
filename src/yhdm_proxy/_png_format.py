from __future__ import annotations

import io
import zlib
import struct
from enum import Enum, auto
from dataclasses import dataclass, field
from collections.abc import AsyncIterator

import typing
from typing import TypeVar, Generic, cast

if typing.TYPE_CHECKING:
    from _typeshed import WriteableBuffer, ReadableBuffer

import trio

from ._recv_helper import (
    PartialResult,
    receive_exactly,
    SupportsReceiveSome,
    SupportsReceiveSomeInto,
    ReceiveStreamWrapper,
)


PNG_SIGNATURE = b"\211PNG\r\n\032\n"


class PNGFormatError(Exception):
    pass


@dataclass
class PNGCheckCRCError(PNGFormatError):
    msg: str
    actual_crc: int
    expected_crc: int

    @classmethod
    def new(cls, actual_crc: int, expected_crc: int) -> PNGCheckCRCError:
        msg = f"CRC checking error. Actual CRC: {actual_crc}, expected: {expected_crc}"
        return cls(msg, actual_crc, expected_crc)


# _generate_next_value_ 应该是一个静态方法，但在 3.8 和 3.9 上如果被装饰为
# 静态方法则 Enum 无法正常工作。必须将此静态方法放到一个基类中，然后继承才行。
#
# https://github.com/python/mypy/issues/7591#issuecomment-652800625
# https://github.com/python/typeshed/issues/10428#issuecomment-1631657737
#
# 在升级到 Python 3.10 后删除此 workaround。
class _PNGChunkTypeBase(Enum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return name.encode("ascii")


class PNGChunkType(_PNGChunkTypeBase):
    IHDR = auto()
    PLTE = auto()
    IDAT = auto()
    IEND = auto()

    tRNS = auto()

    cHRM = auto()
    gAMA = auto()
    iCCP = auto()
    sBIT = auto()
    sRGB = auto()

    tEXt = auto()
    iTXt = auto()
    zTXt = auto()

    bKGD = auto()
    hIST = auto()
    pHYs = auto()
    sPLT = auto()

    tIME = auto()


@dataclass
class DonePNGChunk:
    length: int
    chunk_type: PNGChunkType
    chunk_data: io.BytesIO
    crc: int

    def total_size(self) -> int:
        return 4 + 4 + self.length + 4

    def image_size(self) -> tuple[int, int]:
        if self.chunk_type is not PNGChunkType.IHDR:
            # TODO 具体类型的区块的方法可以放进子类
            raise TypeError("is not a IHDR chunk")
        width, height = struct.unpack("!II", self.chunk_data.read(8))
        self.chunk_data.seek(0)
        return width, height


T_Stream = TypeVar("T_Stream", SupportsReceiveSome, SupportsReceiveSomeInto)


@dataclass
class LazyPNGChunk(Generic[T_Stream]):
    length: int
    chunk_type: PNGChunkType
    offset: int
    stream: T_Stream
    crc: int

    @classmethod
    def new(
        cls,
        length: int,
        chunk_type: PNGChunkType,
        stream: T_Stream,
    ) -> LazyPNGChunk[T_Stream]:
        crc = zlib.crc32(chunk_type.value)
        return LazyPNGChunk(length, chunk_type, 0, stream, crc)

    def _update_crc(self, data: ReadableBuffer) -> None:
        data = cast(bytes, data)  # makes typeshed happy
        self.crc = zlib.crc32(data, self.crc)

    async def receive_some(
        self: LazyPNGChunk[SupportsReceiveSome],
        max_bytes: int,
    ) -> bytes | bytearray:
        diff = self.length - self.offset
        if diff == 0:
            await trio.sleep(0)
            return b""

        if diff < max_bytes:
            max_bytes = diff
        data = await self.stream.receive_some(max_bytes)
        self._update_crc(data)
        self.offset += len(data)
        return data

    async def receive_some_memoryview(self: LazyPNGChunk, max_bytes: int) -> memoryview:
        if isinstance(self.stream, SupportsReceiveSome):
            return memoryview(await self.receive_some(max_bytes))
        with memoryview(bytearray(max_bytes)) as buf:
            received = await self.receive_some_into(buf)
            return buf[:received]

    async def receive_some_into(
        self: LazyPNGChunk[SupportsReceiveSomeInto],
        writable_buf: WriteableBuffer,
    ) -> int:
        diff = self.length - self.offset
        if diff == 0:
            await trio.sleep(0)
            return 0

        with memoryview(writable_buf) as writable_mv:
            buf_length = len(writable_mv)
            if diff < buf_length:
                writable_mv = writable_mv[:diff]
            received = await self.stream.receive_some_into(writable_mv)
            self._update_crc(writable_mv[:received])
            self.offset += received
            return received

    async def verify_crc(self) -> None:
        partial_result: PartialResult[bytes] = PartialResult()
        try:
            data = await receive_exactly(self.stream, 4, partial_result)
        except EOFError as exc:
            raise PNGFormatError("not enough data") from exc
        expected_crc: int = struct.unpack("!I", data)[0]

        if self.crc != expected_crc:
            raise PNGCheckCRCError.new(self.crc, expected_crc)

    async def do_it(self, buf_size: int = 64*1024) -> DonePNGChunk:
        bytes_io = io.BytesIO()
        while True:
            with await self.receive_some_memoryview(buf_size) as mv:
                if len(mv) == 0:
                    if self.offset != self.length:
                        raise PNGFormatError("not enough data")
                    break
                bytes_io.write(mv)
        await self.verify_crc()
        bytes_io.seek(0)
        return DonePNGChunk(self.length, self.chunk_type, bytes_io, self.crc)

    async def skip_it(self, buf_size: int = 64*1024) -> None:
        while True:
            with await self.receive_some_memoryview(buf_size) as mv:
                if len(mv) == 0:
                    if self.offset != self.length:
                        raise PNGFormatError("not enough data")
                    break
        await self.verify_crc()

    def total_size(self) -> int:
        return 4 + 4 + self.length + 4


async def png_chunk_parser(
    stream: SupportsReceiveSome | SupportsReceiveSomeInto,
) -> AsyncIterator[LazyPNGChunk]:
    """See: https://www.w3.org/TR/PNG/#5DataRep"""
    partial_result: PartialResult[bytes] = PartialResult()
    while True:
        try:
            data = await receive_exactly(stream, 4, partial_result)
        except EOFError:
            return
        length: int = struct.unpack("!I", data)[0]

        try:
            data = await receive_exactly(stream, 4, partial_result)
        except EOFError as exc:
            raise PNGFormatError("not enough data") from exc
        if not data.isalpha():
            raise PNGFormatError("chunk type is not in [A-Za-z]")
        try:
            chunk_type = PNGChunkType(data)
        except LookupError as exc:
            raise PNGFormatError(f"unknown chunk type: {data!r}") from exc

        # 于生成器共享同一个 stream，因此必须消费完 PNGChunk 之后再驱动生成器运行
        yield LazyPNGChunk.new(length, chunk_type, stream)
