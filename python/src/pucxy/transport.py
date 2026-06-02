"""Serial transport: framed request/response over a PTY (raw) or two FIFOs.

See spec.md section 4.1. The emulator's serial line is a plain Unix fd, so this
uses os.read/os.write + select (no pyserial). A PTY must be put in raw mode or
its line discipline would mangle the binary protocol (echo, CR/NL translation).
"""

import os
import select
import tty
from dataclasses import dataclass

from .protocol import Response, read_response


class TransportError(Exception):
    pass


class TransportTimeout(TransportError):
    pass


@dataclass(frozen=True)
class Transport:
    """An open serial line. Owns the file descriptors it was told to own."""

    rfd: int
    wfd: int
    owned: frozenset[int]
    timeout: float = 10.0


def open_path(path: str, timeout: float = 10.0) -> Transport:
    """Connect to an existing serial device / PTY slave (emulator started elsewhere)."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    tty.setraw(fd)
    return Transport(rfd=fd, wfd=fd, owned=frozenset({fd}), timeout=timeout)


def open_fifos(serial_in: str, serial_out: str, timeout: float = 10.0) -> Transport:
    """Two named pipes. `serial_in` is the emulator's --serial-in (we write it);
    `serial_out` is its --serial-out (we read it). O_RDWR avoids open-blocking."""
    wfd = os.open(serial_in, os.O_RDWR)
    rfd = os.open(serial_out, os.O_RDWR)
    return Transport(rfd=rfd, wfd=wfd, owned=frozenset({rfd, wfd}), timeout=timeout)


def request(t: Transport, frame: bytes) -> Response:
    """Send a request frame, read one response frame."""
    send_all(t.wfd, frame)
    return read_response(lambda n: recv_exactly(t.rfd, n, t.timeout))


def close(t: Transport) -> None:
    """Best-effort close of every owned fd; never raises."""
    for fd in t.owned:
        close_quiet(fd)


def send_all(fd: int, data: bytes) -> None:
    """Write every byte of `data` to `fd`, handling short writes."""
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def recv_exactly(fd: int, n: int, timeout: float) -> bytes:
    """Read exactly n bytes from `fd`. Raises on per-read timeout or EOF."""
    buf = bytearray()
    while len(buf) < n:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            raise TransportTimeout(f"no serial data after {timeout}s ({len(buf)}/{n} bytes)")
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            raise TransportError("serial line closed (EOF)")
        buf += chunk
    return bytes(buf)


def close_quiet(fd: int) -> None:
    """Best-effort close — drops OSError so cleanup never raises."""
    try:
        os.close(fd)
    except OSError:
        pass
