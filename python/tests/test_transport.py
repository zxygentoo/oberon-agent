"""Serial transport core: send_all, recv_exactly, request. Backed by os.pipe()
so the wire framing is exercised without a real serial device."""

import os
import struct

import pytest

from puck import protocol, transport
from puck.transport import (
    Transport,
    TransportError,
    TransportTimeout,
    close_quiet,
    recv_exactly,
    request,
    send_all,
)


def test_send_all_writes_all_bytes():
    rfd, wfd = os.pipe()
    try:
        send_all(wfd, b"hello world")
        assert os.read(rfd, 64) == b"hello world"
    finally:
        os.close(rfd)
        os.close(wfd)


def test_send_all_loops_on_short_writes(monkeypatch):
    """A short os.write must be retried until every byte is gone."""
    writes: list[bytes] = []
    chunks = iter([3, 4, 999])

    def fake_write(fd, view):
        n = min(next(chunks, len(view)), len(view))
        writes.append(bytes(view[:n]))
        return n

    monkeypatch.setattr(transport.os, "write", fake_write)
    send_all(1, b"hello world!!!")
    assert b"".join(writes) == b"hello world!!!"


def test_recv_exactly_assembles_across_calls():
    rfd, wfd = os.pipe()
    try:
        os.write(wfd, b"hello world")
        assert recv_exactly(rfd, 5, timeout=1.0) == b"hello"
        assert recv_exactly(rfd, 6, timeout=1.0) == b" world"
    finally:
        os.close(rfd)
        os.close(wfd)


def test_recv_exactly_times_out_with_no_data():
    rfd, wfd = os.pipe()
    try:
        with pytest.raises(TransportTimeout) as ei:
            recv_exactly(rfd, 4, timeout=0.05)
        assert "0/4" in str(ei.value)
    finally:
        os.close(rfd)
        os.close(wfd)


def test_recv_exactly_raises_on_eof():
    rfd, wfd = os.pipe()
    os.close(wfd)  # EOF immediately
    try:
        with pytest.raises(TransportError):
            recv_exactly(rfd, 4, timeout=0.5)
    finally:
        os.close(rfd)


def test_recv_exactly_partial_data_then_eof_raises():
    rfd, wfd = os.pipe()
    os.write(wfd, b"ab")
    os.close(wfd)
    try:
        with pytest.raises(TransportError):
            recv_exactly(rfd, 4, timeout=0.5)
    finally:
        os.close(rfd)


def test_close_quiet_is_idempotent():
    rfd, wfd = os.pipe()
    os.close(rfd)
    close_quiet(rfd)  # already closed — OSError swallowed
    close_quiet(wfd)


def test_request_round_trip_via_pipes():
    """Wire-level smoke test: the request frame goes out one pipe and the response
    comes back on the other, with send_all + read_response wired up correctly."""
    r_to_proxy, w_to_proxy = os.pipe()
    r_to_dev, w_to_dev = os.pipe()
    # owned=frozenset() because we manage the fds ourselves in the finally block.
    t = Transport(rfd=r_to_proxy, wfd=w_to_dev, owned=frozenset(), timeout=1.0)
    try:
        # Stage the device's reply where the proxy will read it from.
        resp_frame = (
            bytes([protocol.SYNC_RESP, protocol.ST_OK]) + struct.pack("<I", 2) + b"hi"
        )
        os.write(w_to_proxy, resp_frame)

        resp = request(t, protocol.build_get("M.Mod"))

        assert resp.status == protocol.ST_OK
        assert resp.payload == b"hi"
        # And the request frame the proxy sent is exactly what we asked for.
        assert os.read(r_to_dev, 64) == protocol.build_get("M.Mod")
    finally:
        for fd in (r_to_proxy, w_to_proxy, r_to_dev, w_to_dev):
            close_quiet(fd)
