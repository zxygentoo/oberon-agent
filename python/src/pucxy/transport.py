"""Serial transport: framed request/response over a PTY (raw) or two FIFOs.

See spec.md section 4.1. The emulator's serial line is a plain Unix fd, so this
uses os.read/os.write + select (no pyserial). A PTY must be put in raw mode or
its line discipline would mangle the binary protocol (echo, CR/NL translation).
"""

from __future__ import annotations

import os
import pty
import select
import tty

from .wire import Response, read_response


class TransportError(Exception):
    pass


class TransportTimeout(TransportError):
    pass


class Transport:
    def __init__(
        self, read_fd: int, write_fd: int, owned: tuple[int, ...] = (), timeout: float = 10.0
    ):
        self.rfd = read_fd
        self.wfd = write_fd
        self._owned = set(owned) or {read_fd, write_fd}
        self.timeout = timeout

    def _send_all(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            n = os.write(self.wfd, view)
            view = view[n:]

    def _recv_exactly(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            ready, _, _ = select.select([self.rfd], [], [], self.timeout)
            if not ready:
                raise TransportTimeout(
                    f"no serial data after {self.timeout}s ({len(buf)}/{n} bytes)"
                )
            chunk = os.read(self.rfd, n - len(buf))
            if not chunk:
                raise TransportError("serial line closed (EOF)")
            buf += chunk
        return bytes(buf)

    def request(self, frame: bytes) -> Response:
        self._send_all(frame)
        return read_response(self._recv_exactly)

    def close(self) -> None:
        for fd in self._owned:
            try:
                os.close(fd)
            except OSError:
                pass


def open_pty(timeout: float = 10.0) -> tuple[Transport, str]:
    """Create a raw PTY. Returns (transport, slave_name) — pass slave_name to the
    emulator as both --serial-in and --serial-out."""
    master, slave = pty.openpty()
    tty.setraw(slave)
    tty.setraw(master)
    slave_name = os.ttyname(slave)
    # Keep the slave fd open so the master never sees EOF before the emulator attaches.
    return Transport(master, master, owned=(master, slave), timeout=timeout), slave_name


def open_path(path: str, timeout: float = 10.0) -> Transport:
    """Connect to an existing serial device / PTY slave (emulator started elsewhere)."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    tty.setraw(fd)
    return Transport(fd, fd, owned=(fd,), timeout=timeout)


def open_fifos(serial_in: str, serial_out: str, timeout: float = 10.0) -> Transport:
    """Two named pipes. `serial_in` is the emulator's --serial-in (we write it);
    `serial_out` is its --serial-out (we read it). O_RDWR avoids open-blocking."""
    wfd = os.open(serial_in, os.O_RDWR)
    rfd = os.open(serial_out, os.O_RDWR)
    return Transport(rfd, wfd, owned=(rfd, wfd), timeout=timeout)
