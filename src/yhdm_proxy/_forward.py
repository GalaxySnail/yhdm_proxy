from __future__ import annotations

from http import HTTPStatus
from pprint import pformat
from dataclasses import dataclass
from contextvars import ContextVar
from collections.abc import AsyncIterable, Sequence
from ipaddress import ip_address, IPv4Address, IPv6Address
from contextlib import AsyncExitStack, asynccontextmanager

import typing
from typing import Literal

import trio
# httpcore pinned h11<0.13.0, we need to wait for it
import h11  # type: ignore
import httpx

from ._utils import achain, to_aiter
from ._exceptions import GatewayTimeout
from ._recv_helper import PartialResult, read_at_least
from ._trio_http_server import TrioHTTPWrapper
from ._context import get_client
from ._exceptions import BadGateway

if typing.TYPE_CHECKING:
    from ._trio_http_server import H11Headers


PNG_FORMAT_START = b"\211PNG\r\n\032\n"
PNG_FORMAT_END = b"IEND\xae\x42\x60\x82"


@asynccontextmanager
async def wrap_timeout_error():
    try:
        yield
    except httpx.TimeoutException as exc:
        raise GatewayTimeout from exc


@asynccontextmanager
async def wrap_httpx_request(
    method: Literal["GET", "HEAD"],
    url: str,
    headers: dict[bytes, bytes],
    http_wrapper: TrioHTTPWrapper,
):
    client = get_client()

    response: httpx.Response
    async with \
            wrap_timeout_error(), \
            client.stream(method, url, headers=headers) as response:

        await http_wrapper.log(f"client: httpx {method} on {url}")
        await http_wrapper.log_pprint(
            "client: request headers =",
            "headers",
            dict(response.request.headers),
        )
        await http_wrapper.log(f"client: status = {response.status_code}")
        await http_wrapper.log_pprint(
            "client: headers =",
            "headers",
            dict(response.headers),
        )
        yield response

    assert http_wrapper.conn.our_state in {h11.DONE, h11.MUST_CLOSE}, \
           http_wrapper.conn.our_state


async def modify_m3u8(
    response: httpx.Response,
    http_wrapper: TrioHTTPWrapper,
) -> None:
    addr, port, *_ = http_wrapper.stream.socket.getsockname()
    ip_addr = ip_address(addr)
    path_prefix = "/png/"
    if isinstance(ip_addr, IPv6Address):
        url_prefix = f"http://[{ip_addr}]:{port}{path_prefix}"
    else:  # ipv4
        url_prefix = f"http://{ip_addr}:{port}{path_prefix}"

    content: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith(("http://", "https://")):
            content.append(url_prefix + line)
        else:
            content.append(line)

    body = "".join(content).encode()
    headers = http_wrapper.simple_response_header(
        response.headers["content-type"],
        len(body),
    )
    await http_wrapper.send_response(status_code=200, headers=headers)
    await http_wrapper.send_data(body)
    await http_wrapper.send_eof()


async def forward_m3u8(
    method: Literal["GET", "HEAD"],  # pylint: disable=unused-argument
    url: str,
    headers: dict[bytes, bytes],
    http_wrapper: TrioHTTPWrapper,
) -> None:
    async with wrap_httpx_request("GET", url, headers, http_wrapper) as response:
        # m3u8 文件类型
        # https://developer.apple.com/library/archive/documentation/NetworkingInternet/Conceptual/StreamingMediaGuide/DeployingHTTPLiveStreaming/DeployingHTTPLiveStreaming.html
        content_type = response.headers["Content-Type"]
        if content_type.lower() not in {
            # TODO 暂不处理带分号的情况
            "application/x-mpegurl",
            "application/vnd.apple.mpegurl",
        }:
            await http_wrapper.log(
                f"is not a m3u8 file. Content-Type: {content_type}")
            raise BadGateway

        await modify_m3u8(response, http_wrapper)


async def modify_png_video(
    stream_iter: AsyncIterable[bytes | bytearray],
    http_wrapper: TrioHTTPWrapper,
) -> None:
    raise NotImplementedError


async def forward_png_video(
    method: Literal["GET", "HEAD"],
    url: str,
    headers: dict[bytes, bytes],
    http_wrapper: TrioHTTPWrapper,
) -> None:
    raise NotImplementedError


async def _dispatch_modifier(
    response: httpx.Response,
    http_wrapper: TrioHTTPWrapper,
) -> None:
    # m3u8 文件类型
    # https://developer.apple.com/library/archive/documentation/NetworkingInternet/Conceptual/StreamingMediaGuide/DeployingHTTPLiveStreaming/DeployingHTTPLiveStreaming.html
    if response.headers["Content-Type"].lower() in {
        "application/x-mpegurl",
        "application/vnd.apple.mpegurl",
        # TODO 暂不处理带分号的情况
    } and response.is_success:
        await modify_m3u8(response, http_wrapper)
        return

    # 读取前8个字节，确定文件格式是否为 png
    partial_result: PartialResult[bytes | bytearray] = PartialResult()
    try:
        data = await read_at_least(
            response.aiter_bytes(),
            len(PNG_FORMAT_START),
            partial_result,
        )
    except EOFError:
        pass
    else:
        if data.startswith(PNG_FORMAT_START):
            await modify_png_video(
                achain(to_aiter([data]), response.aiter_bytes()),
                http_wrapper,
            )
            return

    # 不修改数据包，原样传递
    if partial_result.data is not None:
        await http_wrapper.send_data(partial_result.data)
    async for data in response.aiter_bytes():
        await http_wrapper.send_data(data)
    await http_wrapper.send_eof()


async def forward_request(
    request: h11.Request,
    forwarder: Literal["m3u8", "png"],
    url: str,
    http_wrapper: TrioHTTPWrapper,
) -> None:
    method: Literal["GET", "HEAD"]
    if request.method == b"GET":
        method = "GET"
    elif request.method == b"HEAD":
        method = "HEAD"
    else:
        raise RuntimeError("Unreachable")

    # 清理标头中的 host 字段，让 httpx 自己填写
    headers = {k: v for k, v in request.headers if k != b"host"}

    if forwarder == "m3u8":
        await forward_m3u8(method, url, headers, http_wrapper)
    elif forwarder == "png":
        await forward_png_video(method, url, headers, http_wrapper)
    else:
        raise RuntimeError("Unreachable")
