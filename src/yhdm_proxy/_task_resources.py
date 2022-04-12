from __future__ import annotations

from contextvars import ContextVar
from contextlib import AsyncExitStack

import trio


async_exit_stack: ContextVar[AsyncExitStack] = ContextVar("async_exit_stack")


def open_exit_stack() -> AsyncExitStack:
    if async_exit_stack.get(None) is not None:
        raise RuntimeError("async_exit_stack is already opened")

    stack = AsyncExitStack()
    async_exit_stack.set(stack)
    return stack


def register_async_resource(rsrc: trio.abc.AsyncResource) -> None:
    stack = async_exit_stack.get(None)
    if stack is None:
        raise RuntimeError("async_exit_stack is not initialized")

    stack.push_async_exit(rsrc)


