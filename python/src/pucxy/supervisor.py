"""Emulator supervisor: spawn bin/risc with serial wired to the proxy, and
reset it on a hung/trapped server. See spec.md sections 4.1, 4.6.
"""

from __future__ import annotations

import subprocess
import time


class Supervisor:
    def __init__(
        self, risc: str, image: str, serial_name: str, mem: int | None = None, leds: bool = False
    ):
        self.risc = risc
        self.image = image
        self.serial = serial_name
        self.mem = mem
        self.leds = leds
        self.proc: subprocess.Popen | None = None

    def _cmd(self) -> list[str]:
        cmd = [self.risc, "--serial-in", self.serial, "--serial-out", self.serial]
        if self.mem:
            cmd += ["--mem", str(self.mem)]
        if self.leds:
            cmd.append("--leds")
        cmd.append(self.image)
        return cmd

    def start(self) -> None:
        if self.is_alive():
            return
        self.proc = subprocess.Popen(self._cmd())

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def reset(self) -> None:
        self.stop()
        time.sleep(0.2)
        self.start()

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def __enter__(self) -> "Supervisor":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
