from __future__ import annotations

import sys

from typing import TypeVar, Iterable, AsyncIterable, AsyncIterator


T = TypeVar("T")


if sys.version_info >= (3, 10):
    anext = anext
else:
    async def anext(aiterator: AsyncIterator[T]) -> T:
        return aiterator.__anext__()


async def to_aiter(iterable: Iterable[T]) -> AsyncIterator[T]:
    """convert an iterable to an async iterator"""
    for item in iterable:
        yield item


async def achain(*iterables: AsyncIterable[T]) -> AsyncIterator[T]:
    """async version from itertools.chain"""
    for iterable in iterables:
        async for item in iterable:
            yield item
