"""Wire protocol (proxy <-> Oberon Agent.Mod). See spec.md section 4.2.

Host is master: build a REQUEST, send it, then read one RESPONSE.
All multi-byte integers are unsigned little-endian.
"""

import struct
from collections.abc import Callable
from dataclasses import dataclass

SYNC_REQ = 0xA5
SYNC_RESP = 0x5A

OP_PUT = 1
OP_GET = 2
OP_CALL = 3

ST_OK = 0
ST_NOT_FOUND = 1
ST_TRAPPED = 2
ST_ERROR = 3

STATUS_NAMES = {ST_OK: "ok", ST_NOT_FOUND: "not_found", ST_TRAPPED: "trapped", ST_ERROR: "error"}


class ProtocolError(Exception):
    """A malformed or out-of-frame response."""


@dataclass
class Response:
    status: int
    payload: bytes

    @property
    def ok(self) -> bool:
        return self.status == ST_OK


def build_put(name: str, data: bytes) -> bytes:
    return bytes([SYNC_REQ, OP_PUT]) + _name_field(name) + struct.pack("<I", len(data)) + data


def build_get(name: str) -> bytes:
    return bytes([SYNC_REQ, OP_GET]) + _name_field(name)


def build_call(cmd: str, par: bytes = b"") -> bytes:
    return bytes([SYNC_REQ, OP_CALL]) + _name_field(cmd) + struct.pack("<I", len(par)) + par


def _name_field(name: str) -> bytes:
    b = name.encode("latin1")
    if not 1 <= len(b) <= 255:
        raise ValueError(f"name length {len(b)} out of range 1..255: {name!r}")
    return bytes([len(b)]) + b


def read_response(recv: Callable[[int], bytes]) -> Response:
    """Read a RESPONSE frame using `recv(n)` (must return exactly n bytes)."""
    sync = recv(1)
    if not sync or sync[0] != SYNC_RESP:
        raise ProtocolError(f"bad response sync byte: {sync!r}")
    status = recv(1)[0]
    (length,) = struct.unpack("<I", recv(4))
    payload = recv(length) if length else b""
    return Response(status, payload)
