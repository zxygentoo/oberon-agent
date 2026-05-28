"""Agent tools implemented on the three wire ops. See spec.md section 4.3.

Each method returns a JSON-serializable dict (the tool result the LLM sees).
"""

from __future__ import annotations

import re
from typing import Protocol

from . import compile_log, wire
from .compile_log import CompileResult
from .oberon_text import from_oberon, to_oberon
from .toolspec import TOOL_NAMES


class Device(Protocol):
    """Anything that answers a wire request: a Transport, or a test fake."""

    def request(self, frame: bytes) -> wire.Response: ...


class AgentTools:
    def __init__(self, transport: Device):
        self.t = transport

    # --- files ---

    def read_file(self, path: str) -> dict:
        r = self.t.request(wire.build_get(path))
        if r.status == wire.ST_NOT_FOUND:
            return {"error": "not_found", "path": path}
        if not r.ok:
            return {"error": wire.STATUS_NAMES.get(r.status, "error")}
        return {"content": from_oberon(r.payload)}

    def write_file(self, path: str, content: str) -> dict:
        r = self.t.request(wire.build_put(path, to_oberon(content)))
        if not r.ok:
            return {"error": wire.STATUS_NAMES.get(r.status, "error")}
        return {"ok": True, "path": path, "bytes": len(content)}

    def edit_file(self, path: str, old: str, new: str) -> dict:
        got = self.read_file(path)
        if "error" in got:
            return got
        content = got["content"]
        count = content.count(old)
        if count == 0:
            return {"error": "not_found", "detail": "old string not present in file"}
        if count > 1:
            return {"error": "not_unique", "detail": f"old string occurs {count} times"}
        written = self.write_file(path, content.replace(old, new, 1))
        if "error" in written:
            return written
        return {"ok": True, "path": path, "replaced": 1}

    def delete_file(self, path: str) -> dict:
        log = self._call_log("System.DeleteFiles", path)
        if "failed" in log:
            return {"error": "not_found", "log": log.strip()}
        return {"ok": True, "path": path}

    # --- listing / modules ---

    def list_files(self, prefix: str = "") -> dict:
        files = []
        for line in self._call_log("Agent.ListFiles", prefix).splitlines():
            parts = line.split("\t")
            if not parts[0]:
                continue
            entry: dict = {"name": parts[0]}
            if len(parts) > 1 and parts[1].strip().isdigit():
                entry["size"] = int(parts[1].strip())
            if len(parts) > 2 and parts[2].strip():
                entry["date"] = parts[2].strip()
            files.append(entry)
        return {"files": files}

    def list_modules(self) -> dict:
        mods = []
        for line in self._call_log("Agent.ListModules", "").splitlines():
            parts = line.split("\t")
            if not parts[0]:
                continue
            m: dict = {"name": parts[0]}
            if len(parts) > 1 and parts[1].strip().lstrip("-").isdigit():
                m["refcnt"] = int(parts[1].strip())
            if len(parts) > 2 and parts[2].strip():
                m["code_addr"] = parts[2].strip()
            mods.append(m)
        return {"modules": mods}

    def load_module(self, name: str) -> dict:
        log = self._call_log("Agent.Load", name).strip()
        if log.startswith("loaded"):
            return {"ok": True, "module": name}
        m = re.search(r"res=(\d+)", log)
        return {"error": "load_failed", "res": int(m.group(1)) if m else None, "log": log}

    def unload_module(self, name: str) -> dict:
        log = self._call_log("System.Free", name)
        if "failed" in log:
            return {"error": "in_use", "log": log.strip()}
        return {"ok": True, "module": name}

    # --- compile / run ---

    def compile(self, name: str, new_symbol: bool = False) -> dict:
        par = name + ("/s" if new_symbol else "")
        r = self.t.request(wire.build_call("ORP.Compile", to_oberon(par)))
        log = from_oberon(r.payload)
        source = self.read_file(name).get("content")
        return _result_to_dict(compile_log.parse(log, source))

    def run_command(self, cmd: str, args: str = "") -> dict:
        r = self.t.request(wire.build_call(cmd, to_oberon(args)))
        return {
            "status": wire.STATUS_NAMES.get(r.status, str(r.status)),
            "ok": r.ok,
            "log": from_oberon(r.payload),
        }

    # --- internals ---

    def _call_log(self, cmd: str, args: str) -> str:
        return from_oberon(self.t.request(wire.build_call(cmd, to_oberon(args))).payload)

    def dispatch(self, name: str, args: dict) -> dict:
        if name not in TOOL_NAMES:
            return {"error": f"unknown tool {name!r}"}
        try:
            return getattr(self, name)(**args)
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}


def _result_to_dict(r: CompileResult) -> dict:
    diags = []
    for d in r.diagnostics:
        item: dict = {"msg": d.msg, "offset": d.offset}
        if d.line is not None:
            item["line"] = d.line
            item["col"] = d.col
            item["source_line"] = d.source_line
            item["context"] = [{"line": ln, "text": tx} for ln, tx in d.context]
        diags.append(item)
    return {
        "ok": r.ok,
        "module": r.module,
        "symbol_file": r.symbol_file,
        "code_bytes": r.code_bytes,
        "data_bytes": r.data_bytes,
        "key": r.key,
        "diagnostics": diags,
    }
