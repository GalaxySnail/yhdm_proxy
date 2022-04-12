from __future__ import annotations

from http import HTTPStatus
from dataclasses import dataclass

from typing import ClassVar


class CloseConnection(Exception):
    """表示应该关闭 tcp 连接。
    这个异常不应该被中途捕获，而是自动传播到 handler 顶层，
    被捕获后关闭连接
    """


@dataclass
class HTTPStatusError(Exception):
    """用来表示 HTTP 错误状态的基类"""
    print_tb: bool = True
    code: ClassVar[HTTPStatus]

    def __str__(self):
        return f"{self.code.value} {self.code.phrase}"


@dataclass
class NotFound(HTTPStatusError):
    print_tb: bool = False
    code = HTTPStatus.NOT_FOUND


@dataclass
class MethodNotAllowed(HTTPStatusError):
    print_tb: bool = False
    code = HTTPStatus.METHOD_NOT_ALLOWED


@dataclass
class InternalServerError(HTTPStatusError):
    print_tb: bool = True
    code = HTTPStatus.INTERNAL_SERVER_ERROR


@dataclass
class BadGateway(HTTPStatusError):
    print_tb: bool = False
    code = HTTPStatus.BAD_GATEWAY


@dataclass
class GatewayTimeout(HTTPStatusError):
    print_tb: bool = False
    code = HTTPStatus.GATEWAY_TIMEOUT
