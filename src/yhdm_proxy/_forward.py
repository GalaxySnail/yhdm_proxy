from __future__ import annotations

from http import HTTPStatus
from collections.abc import AsyncIterable, AsyncIterator
from ipaddress import ip_address, IPv4Address, IPv6Address
from contextlib import asynccontextmanager

import typing
from typing import Literal

# httpcore pinned h11<0.13.0, we need to wait for it
import h11  # type: ignore
import httpx

from ._utils import achain, to_aiter
from ._exceptions import GatewayTimeout
from ._recv_helper import PartialResult, read_at_least, find_in_stream
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
    elif isinstance(ip_addr, IPv4Address):
        url_prefix = f"http://{ip_addr}:{port}{path_prefix}"
    else:
        raise RuntimeError("Unreachable")

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
    method: Literal["GET", "HEAD"],
    response: httpx.Response,
    http_wrapper: TrioHTTPWrapper,
) -> None:
    data: bytes | bytearray
    length = int(response.headers["Content-Length"])
    stream_iter = response.aiter_bytes()
    # 响应的格式应该是 FFmpeg Service01, 因此这里简单使用二进制流类型
    # https://developer.mozilla.org/docs/Web/HTTP/Basics_of_HTTP/MIME_types#applicationoctet-stream
    content_type = "application/octet-stream"

    if method == "HEAD":
        # HEAD 请求，没有响应体所以没法判断是不是符合要求的文件格式
        # 于是这里直接硬编码长度，认为删掉流的前 120 字节
        res_headers = \
            http_wrapper.simple_response_header(content_type, length - 120)
        await http_wrapper.send_response(
            status_code=200,
            headers=res_headers,
        )
        await http_wrapper.send_eof()
        return

    # 处理 GET 请求
    assert method == "GET", method

    # 读取前8个字节，确定文件格式是否为 png
    partial_result: PartialResult[bytes | bytearray] = PartialResult()
    try:
        start_data = await read_at_least(
            stream_iter,
            len(PNG_FORMAT_START),
            partial_result,
        )
    except EOFError as exc:
        # 远程服务器的响应少于 8 字节，这不应该发生
        await http_wrapper.log("client: content is too short")
        raise BadGateway from exc

    assert partial_result.data is None

    # 如果响应体不是 png 文件，只好不做修改，原样转发流
    if not start_data.startswith(PNG_FORMAT_START):
        await http_wrapper.log("not a png file, forward it directly")
        await http_wrapper.send_response(
            status_code=response.status_code,
            headers=response.headers.items(),
            reason=response.reason_phrase,
        )
        await http_wrapper.send_data(start_data)
        async for data in response.aiter_bytes():
            await http_wrapper.send_data(data)
        await http_wrapper.send_eof()
        return

    # 解析 png 文件结尾，并忽略它
    offset = 0
    async for data, find in find_in_stream(
        achain(to_aiter([start_data]), stream_iter),
        PNG_FORMAT_END,
    ):
        if find:
            break
        offset += len(data)
    else:
        # 迭代完了整个流仍没有找到 png 文件结尾，这不应该发生
        await http_wrapper.log("PNG file end not found")
        raise BadGateway

    # 此时，data 变量一定以 PNG_FORMAT_END 开头
    assert data.startswith(PNG_FORMAT_END)
    await http_wrapper.log(f"PNG length: {offset + len(PNG_FORMAT_END)}")

    headers = http_wrapper.simple_response_header(
        content_type, length - offset - len(PNG_FORMAT_END))
    await http_wrapper.send_response(status_code=200, headers=headers)
    await http_wrapper.send_data(data[len(PNG_FORMAT_END):])
    async for data in stream_iter:
        await http_wrapper.send_data(data)
    await http_wrapper.send_eof()


async def forward_png_video(
    method: Literal["GET", "HEAD"],
    url: str,
    headers: dict[bytes, bytes],
    http_wrapper: TrioHTTPWrapper,
) -> None:
    async with wrap_httpx_request(method, url, headers, http_wrapper) as response:
        if not response.is_success:
            code = HTTPStatus(response.status_code)
            await http_wrapper.log(
                f"client: request failed with {code.value} {code.phrase}")
            raise BadGateway

        await modify_png_video(method, response, http_wrapper)


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
