from __future__ import annotations

import re
import traceback
from http import HTTPStatus
from dataclasses import dataclass
from contextvars import ContextVar

from typing import Any, ClassVar

import trio
# httpcore pinned h11<0.13.0, we need to wait for it
import h11  # type: ignore

from ._forward import forward_request
from ._trio_http_server import TrioHTTPWrapper
from ._context import request as context_request
from ._exceptions import (
    CloseConnection,
    HTTPStatusError,
    NotFound,
    MethodNotAllowed,
)


M3U8_PATTERN = re.compile(r"^/m3u8/(https?://.*)")
PNG_PATTERN = re.compile(r"^/png/(https?://.*)")


async def handle_a_request(http_wrapper: TrioHTTPWrapper) -> None:
    """处理一个 GET 请求，并根据 target 分发到具体的处理函数上"""
    assert http_wrapper.conn.states == {h11.CLIENT: h11.IDLE, h11.SERVER: h11.IDLE}

    # 读取请求头
    with trio.fail_after(http_wrapper.timeout):
        event = await http_wrapper.next_event()

    if isinstance(event, h11.ConnectionClosed):
        assert http_wrapper.conn.our_state is h11.MUST_CLOSE
        raise CloseConnection
    assert isinstance(event, h11.Request), f"event = {event}"

    request: h11.Request = event
    context_request.set(request)
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
    target: str = request.target.decode()

    match = re.match(M3U8_PATTERN, target)
    if match:
        url = match.group(1)
        await forward_request(request, "m3u8", url, http_wrapper)
        return

    match = re.match(PNG_PATTERN, target)
    if match:
        url = match.group(1)
        await forward_request(request, "png", url, http_wrapper)
        return

    raise NotFound


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
    headers = http_wrapper.simple_response_header(
        "text/html; charset=utf-8",
        len(body),
    )
    await http_wrapper.send_response(
        status_code=status_code,
        headers=headers,
        reason=status_code.phrase.encode(),
    )
    await http_wrapper.send_data(body)
    await http_wrapper.send_eof()


async def try_to_send_error_response(
    http_wrapper: TrioHTTPWrapper,
    exc: Exception,
) -> None:
    """如果当前状态还有机会发送HTTP响应，则根据异常类型发送一个错误响应"""
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


async def try_to_start_next_cycle(http_wrapper: TrioHTTPWrapper) -> None:
    """一个请求处理完毕，尝试启动下一个处理循环。
    如果正常返回，则说明成功进入下一个循环
    如果引发 CloseConnection ，则正常关闭连接
    如果引发 h11.ProtocolError ，则说明出现了意料之外的问题
    """
    # 请求处理完毕，如果不启用 keep alive 则关闭连接
    if http_wrapper.conn.our_state is h11.MUST_CLOSE:
        raise CloseConnection

    # 否则，尝试重置 HTTP 状态，准备处理下一个请求
    try:
        http_wrapper.conn.start_next_cycle()
    except h11.ProtocolError as exc:
        await http_wrapper.log(f"Unexpected state {http_wrapper.conn.states}")
        await try_to_send_error_response(http_wrapper, exc)
        raise

    await http_wrapper.log("start a new cycle")


async def http_handler(http_wrapper: TrioHTTPWrapper) -> None:
    while True:
        # 外层的异常处理，根据异常决定关闭连接还是启动下一次循环
        # 并且打印某些异常的回溯
        try:
            # 内层的异常处理，用于根据异常来发送错误响应，之后重新引发向上传播
            try:
                await handle_a_request(http_wrapper)
                assert http_wrapper.conn.our_state in {h11.DONE, h11.MUST_CLOSE}
            except Exception as exc:
                await try_to_send_error_response(http_wrapper, exc)
                raise

        # 如果之前发生了超时，则放弃连接
        except trio.TooSlowError as exc:
            raise CloseConnection from exc

        # 如果引发了 CloseConnection, 直接向上传播
        except CloseConnection:
            raise

        # h11.RemoteProtocolError 是对方客户端行为不正确导致的，不需要记录异常
        except h11.RemoteProtocolError:
            pass

        # 如果 trio.BrokenResourceError 是由对方关闭 tcp 连接导致
        # 只需要打印一条日志即可，不需要记录异常回溯
        except trio.BrokenResourceError as exc:
            if isinstance(exc.__cause__, ConnectionResetError):
                errmsg = \
                    traceback.format_exception_only(type(exc), exc)[0].rstrip()
                await http_wrapper.log(
                    f"Remote client closed the tcp connection ({errmsg})")
            else:
                await http_wrapper.log_exception("unexpected exception:")

            raise CloseConnection from exc

        # HTTPStatusError 不需要向上传播，而是在此处处理
        # 如果 print_tb 属性为真，则就地打印异常回溯
        except HTTPStatusError as exc:
            if exc.print_tb:
                await http_wrapper.log_exception("http status error:")

        # 其它异常应该都是意外的，一律在此处捕获并打印回溯
        except Exception as exc:  # pylint: disable=W0703
            await http_wrapper.log_exception("unexpected exception:")

        # 到此为止，引发了异常的代码路径的异常都已经处理
        # 需要打印回溯的异常都已经打印了回溯，这里不再需要处理异常信息
        # 如果之前引发了 KeyboardInterrupted 异常则会直接传播
        # 于是尝试启动下一个循环，这里引发的异常会直接被上层捕获
        await try_to_start_next_cycle(http_wrapper)
