import struct

import pytest

from pucxy import wire


def reader(buf: bytes):
    pos = 0

    def recv(n: int) -> bytes:
        nonlocal pos
        chunk = buf[pos : pos + n]
        pos += n
        return chunk

    return recv


def test_build_put_layout():
    f = wire.build_put("Agent.Mod", b"abc")
    assert f[0] == wire.SYNC_REQ
    assert f[1] == wire.OP_PUT
    assert f[2] == len("Agent.Mod")
    assert f[3:12] == b"Agent.Mod"
    assert f[12:16] == struct.pack("<I", 3)
    assert f[16:] == b"abc"


def test_build_get_layout():
    assert wire.build_get("X") == bytes([wire.SYNC_REQ, wire.OP_GET, 1]) + b"X"


def test_build_call_layout():
    f = wire.build_call("Mod.Proc", b"par")
    assert f[:2] == bytes([wire.SYNC_REQ, wire.OP_CALL])
    assert f[2] == len("Mod.Proc")
    assert f.endswith(struct.pack("<I", 3) + b"par")


def test_read_response_ok():
    buf = bytes([wire.SYNC_RESP, wire.ST_OK]) + struct.pack("<I", 3) + b"xyz"
    r = wire.read_response(reader(buf))
    assert r.status == wire.ST_OK
    assert r.payload == b"xyz"
    assert r.ok


def test_read_response_empty_payload():
    buf = bytes([wire.SYNC_RESP, wire.ST_NOT_FOUND]) + struct.pack("<I", 0)
    r = wire.read_response(reader(buf))
    assert r.status == wire.ST_NOT_FOUND
    assert r.payload == b""
    assert not r.ok


def test_read_response_bad_sync():
    with pytest.raises(wire.ProtocolError):
        wire.read_response(reader(b"\x00\x00\x00\x00\x00\x00"))


def test_name_length_validated():
    with pytest.raises(ValueError):
        wire.build_get("x" * 256)
    with pytest.raises(ValueError):
        wire.build_get("")
