from __future__ import annotations

from functools import partial
from dataclasses import dataclass
import trio


@dataclass
class Logger(trio.abc.AsyncResource):
    send_channel: trio.MemorySendChannel[str]

    async def log(self, msg: str) -> None:
        await self.send_channel.send(msg)

    async def aclose(self):
        await self.send_channel.aclose()


async def _get_logger(send_channel: trio.MemorySendChannel[str]) -> Logger:
    return Logger(send_channel.clone())


async def log_task(task_status=trio.TASK_STATUS_IGNORED) -> None:
    send_channel: trio.abc.SendChannel[str]
    recv_channel: trio.abc.ReceiveChannel[str]

    send_channel, recv_channel = trio.open_memory_channel(0)
    task_status.started(partial(_get_logger, send_channel))

    async with recv_channel, send_channel:
        async for msg in recv_channel:
            await trio.to_thread.run_sync(print, msg)
