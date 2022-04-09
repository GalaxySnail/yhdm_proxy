"""Copied from stdlib imghdr"""

from __future__ import annotations


def is_png(head: bytes | bytearray) -> bool:
    return head.startswith(b"\211PNG\r\n\032\n")
