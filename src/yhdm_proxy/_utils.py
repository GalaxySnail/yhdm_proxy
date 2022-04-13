from __future__ import annotations

import sys

from typing import TypeVar, Iterable, AsyncIterable, AsyncIterator


T = TypeVar("T")


if sys.version_info >= (3, 10):
    aiter = aiter
    anext = anext
else:
    def aiter(aiterable: AsyncIterable[T]) -> AsyncIterator[T]:
        return aiterable.__aiter__()

    async def anext(aiterator: AsyncIterator[T]) -> T:
        return await aiterator.__anext__()


async def to_aiter(iterable: Iterable[T]) -> AsyncIterator[T]:
    """convert an iterable to an async iterator"""
    for item in iterable:
        yield item


async def achain(*iterables: AsyncIterable[T]) -> AsyncIterator[T]:
    """async version from itertools.chain"""
    for iterable in iterables:
        async for item in iterable:
            yield item
