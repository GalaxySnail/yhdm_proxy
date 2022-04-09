from __future__ import annotations

import h11  # type: ignore
import httpx

from ._trio_http_server import TrioHTTPWrapper


async def get_m3u8(
    request: h11.Request,
    url: str,
    http_wrapper: TrioHTTPWrapper,
) -> None:
    raise NotImplementedError


async def get_png_video(
    request: h11.Request,
    video_id: int,
    http_wrapper: TrioHTTPWrapper,
) -> None:
    raise NotImplementedError
