import struct

import pytest

from puck import protocol


def reader(buf: bytes):
    pos = 0

    def recv(n: int) -> bytes:
        nonlocal pos
        chunk = buf[pos : pos + n]
        pos += n
        return chunk

    return recv


def test_build_put_layout():
    name = "Puck.Mod"
    nl = len(name)
    f = protocol.build_put(name, b"abc")
    assert f[0] == protocol.SYNC_REQ
    assert f[1] == protocol.OP_PUT
    assert f[2] == nl
    assert f[3 : 3 + nl] == name.encode()
    assert f[3 + nl : 3 + nl + 4] == struct.pack("<I", 3)
    assert f[3 + nl + 4 :] == b"abc"


def test_build_get_layout():
    assert protocol.build_get("X") == bytes([protocol.SYNC_REQ, protocol.OP_GET, 1]) + b"X"


def test_build_call_layout():
    f = protocol.build_call("Mod.Proc", b"par")
    assert f[:2] == bytes([protocol.SYNC_REQ, protocol.OP_CALL])
    assert f[2] == len("Mod.Proc")
    assert f.endswith(struct.pack("<I", 3) + b"par")


def test_read_response_ok():
    buf = bytes([protocol.SYNC_RESP, protocol.ST_OK]) + struct.pack("<I", 3) + b"xyz"
    r = protocol.read_response(reader(buf))
    assert r.status == protocol.ST_OK
    assert r.payload == b"xyz"
    assert r.ok


def test_read_response_empty_payload():
    buf = bytes([protocol.SYNC_RESP, protocol.ST_NOT_FOUND]) + struct.pack("<I", 0)
    r = protocol.read_response(reader(buf))
    assert r.status == protocol.ST_NOT_FOUND
    assert r.payload == b""
    assert not r.ok


def test_read_response_bad_sync():
    with pytest.raises(protocol.ProtocolError):
        protocol.read_response(reader(b"\x00\x00\x00\x00\x00\x00"))


def test_name_length_validated():
    with pytest.raises(ValueError):
        protocol.build_get("x" * 256)
    with pytest.raises(ValueError):
        protocol.build_get("")


def test_read_response_empty_sync_raises_protocol_error():
    """EOF on the sync byte read (recv returns b'') is a distinct branch from a
    wrong sync byte — both must raise ProtocolError."""
    with pytest.raises(protocol.ProtocolError):
        protocol.read_response(reader(b""))


def test_build_call_with_default_empty_par():
    """Length prefix is still written for a zero-length par."""
    f = protocol.build_call("Mod.Proc")
    assert f[-4:] == struct.pack("<I", 0)
