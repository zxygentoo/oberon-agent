"""In-memory fake of the Oberon side: decode wire frames, serve files and a few
commands. Lets tools be tested end-to-end without the emulator."""

from __future__ import annotations

import struct
from typing import Callable

from pucxy import wire

CallHandler = Callable[[str, bytes], tuple[int, bytes] | None]


class FakeTransport:
    def __init__(self, files: dict[str, bytes] | None = None, call: CallHandler | None = None):
        self.files: dict[str, bytes] = dict(files or {})
        self.modules: set[str] = {"System", "Oberon", "Agent"}
        self._call = call

    def request(self, frame: bytes) -> wire.Response:
        assert frame[0] == wire.SYNC_REQ, "bad request sync"
        op = frame[1]
        i = 2
        nlen = frame[i]
        i += 1
        name = frame[i : i + nlen].decode("latin1")
        i += nlen
        if op == wire.OP_GET:
            data = self.files.get(name)
            if data is None:
                return wire.Response(wire.ST_NOT_FOUND, b"")
            return wire.Response(wire.ST_OK, data)
        if op == wire.OP_PUT:
            (dlen,) = struct.unpack("<I", frame[i : i + 4])
            i += 4
            self.files[name] = frame[i : i + dlen]
            return wire.Response(wire.ST_OK, b"")
        if op == wire.OP_CALL:
            (plen,) = struct.unpack("<I", frame[i : i + 4])
            i += 4
            par = frame[i : i + plen]
            status, log = self._dispatch_call(name, par)
            return wire.Response(status, log)
        raise AssertionError(f"bad op {op}")

    def _dispatch_call(self, cmd: str, par: bytes) -> tuple[int, bytes]:
        if self._call:
            out = self._call(cmd, par)
            if out is not None:
                return out
        arg = par.decode("latin1").replace("\r", "\n").strip()
        if cmd == "System.DeleteFiles":
            if arg in self.files:
                del self.files[arg]
                return wire.ST_OK, f"System.DeleteFiles\n{arg} deleting\n".encode()
            return wire.ST_OK, f"System.DeleteFiles\n{arg} deleting failed\n".encode()
        if cmd == "System.Free":
            self.modules.discard(arg)
            return wire.ST_OK, f"System.Free\n{arg} unloading\n".encode()
        if cmd == "Agent.Load":
            self.modules.add(arg)
            return wire.ST_OK, f"loaded {arg}\n".encode()
        if cmd == "Agent.ListFiles":
            lines = [f"{n}\t{len(d)}\t01.01.24 00:00:00" for n, d in sorted(self.files.items())]
            return wire.ST_OK, ("\n".join(lines) + "\n").encode()
        if cmd == "Agent.ListModules":
            lines = [f"{m}\t0\t 00001000" for m in sorted(self.modules)]
            return wire.ST_OK, ("\n".join(lines) + "\n").encode()
        return wire.ST_OK, b""

    def close(self) -> None:
        pass
