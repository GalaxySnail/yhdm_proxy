from __future__ import annotations

import re
from http import HTTPStatus

from typing import Any, ClassVar

import trio
# httpcore pinned h11<0.13.0, we need to wait for it
import h11  # type: ignore

from . import _log
from ._recv_helper import receive_exactly, PartialResult
from ._trio_http_server import TrioHTTPWrapper
from ._get_data import get_m3u8, get_png_video


class CloseConnection(Exception):
    """表示应该关闭 tcp 连接。
    这个异常不应该被中途捕获，而是自动传播到 handler 顶层，
    被捕获后关闭连接
    """


class HTTPStatusError(Exception):
    """用来表示 HTTP 错误状态的基类"""
    code: ClassVar[HTTPStatus]


class NotFound(HTTPStatusError):
    code = HTTPStatus.NOT_FOUND


class MethodNotAllowed(HTTPStatusError):
    code = HTTPStatus.METHOD_NOT_ALLOWED


def simple_response_header(
    http_wrapper: TrioHTTPWrapper,
    content_type: str | None,
    content_length: int,
) -> list[tuple[str, Any]]:
    headers = http_wrapper.basic_headers()
    if content_type is not None:
        headers.append(("Content-Type", content_type))
    headers.append(("Content-Length", str(content_length)))
    return headers


async def send_error_response(
    http_wrapper: TrioHTTPWrapper,
    status_code: HTTPStatus,
) -> None:
    """根据 HTTP 错误码发送错误响应"""
    body = (
        "<html><body><center>"
        f"<h1>{status_code} {HTTPStatus(status_code).phrase}</h1>"
        "</center></body></html>\n"
    ).encode("utf-8")
    headers = simple_response_header(
        http_wrapper, "text/html; charset=utf-8", len(body))
    res = h11.Response(status_code=status_code, headers=headers)
    await http_wrapper.send(res)
    await http_wrapper.send(h11.Data(data=body))
    await http_wrapper.send(h11.EndOfMessage())


M3U8_TARGET_PATTERN = re.compile(rb"^/m3u8/(https?://.*)")
DATA_TARGET_PATTERN = re.compile(rb"^/data/(\d+)$")


async def handle_a_request(http_wrapper: TrioHTTPWrapper) -> None:
    """处理一个 GET 请求，并根据 target 分发到具体的处理函数上"""
    assert http_wrapper.conn.states == {h11.CLIENT: h11.IDLE, h11.SERVER: h11.IDLE}

    # 读取请求头
    with trio.fail_after(http_wrapper.timeout):
        event = await http_wrapper.next_event()
    if isinstance(event, h11.ConnectionClosed):
        raise CloseConnection
    assert isinstance(event, h11.Request), f"event = {event}"

    request: h11.Request = event
    await http_wrapper.log(
        f"{request.method.decode()} on {request.target.decode()}"
    )
    if request.method not in {b"GET", b"HEAD"}:
        raise MethodNotAllowed

    # 读取 EndOfMessage
    with trio.fail_after(http_wrapper.timeout):
        event = await http_wrapper.next_event()
    if not isinstance(event, h11.EndOfMessage):
        raise h11.RemoteProtocolError("content for GET method is not allowed")

    # 根据 target 分派请求处理
    target: bytes = request.target
    match = M3U8_TARGET_PATTERN.search(target)
    if match:
        await get_m3u8(request, match.group(1).decode(), http_wrapper)
        return
    match = DATA_TARGET_PATTERN.search(target)
    if match:
        await get_png_video(request, int(match.group(1)), http_wrapper)
        return

    raise NotFound


async def try_to_send_error_response(
    http_wrapper: TrioHTTPWrapper,
    exc: Exception,
) -> None:
    if http_wrapper.conn.our_state not in {h11.IDLE, h11.SEND_RESPONSE}:
        return

    if isinstance(exc, h11.RemoteProtocolError):
        status_code = HTTPStatus.BAD_REQUEST
    elif isinstance(exc, trio.TooSlowError):
        status_code = HTTPStatus.REQUEST_TIMEOUT
    elif isinstance(exc, HTTPStatusError):
        status_code = exc.code
    else:
        status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    await http_wrapper.log(
        f"Response {status_code.value} {status_code.phrase}"
    )
    await send_error_response(http_wrapper, status_code)
    assert http_wrapper.conn.our_state in {h11.DONE, h11.MUST_CLOSE}


async def try_to_start_next_cycle(
    http_wrapper: TrioHTTPWrapper,
    last_exc: Exception | None = None
) -> None:
    """一个请求处理完毕，尝试启动下一个处理循环。
    如果正常返回，则说明成功进入下一个循环
    如果引发 CloseConnection ，则正常关闭连接
    如果引发 h11.ProtocolError ，则说明出现了意料之外的问题
    """
    # 如果之前发生了 AssertionError，则重新引发它
    if isinstance(last_exc, AssertionError):
        raise last_exc
    # 如果之前发生了超时，则放弃连接
    if isinstance(last_exc, trio.TooSlowError):
        raise CloseConnection
    # 请求处理完毕，如果不启用 keep alive 则关闭连接
    if http_wrapper.conn.our_state is h11.MUST_CLOSE:
        raise CloseConnection

    # 否则，尝试重置 HTTP 状态，准备处理下一个请求
    try:
        http_wrapper.conn.start_next_cycle()
    except h11.ProtocolError as exc:
        if last_exc is not None:
            exc.__cause__ = last_exc
        await http_wrapper.log(f"Unexpected state {http_wrapper.conn.states}")
        await try_to_send_error_response(http_wrapper, exc)
        raise

    await http_wrapper.log("start a new cycle")


async def http_handler(http_wrapper: TrioHTTPWrapper) -> None:
    while True:
        last_exc: Exception | None = None
        try:
            await handle_a_request(http_wrapper)
            assert http_wrapper.conn.our_state in {h11.DONE, h11.MUST_CLOSE}
        except AssertionError as exc:
            last_exc = exc
            raise
        except Exception as exc:  # pylint: disable=W0703
            last_exc = exc
            await try_to_send_error_response(http_wrapper, exc)
        finally:
            await try_to_start_next_cycle(http_wrapper, last_exc)
