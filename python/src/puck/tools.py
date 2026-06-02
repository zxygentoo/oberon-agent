"""Host-side tools implemented on the three wire ops. See spec.md section 4.3.

Each function returns a JSON-serializable dict (the tool result the LLM sees).
A Device is anything that turns a wire request frame into a wire response —
a transport bound via `partial(transport.request, t)` or a test fake.
"""

import re
from collections.abc import Callable
from typing import TypeAlias

from . import protocol
from .text import from_oberon, to_oberon

Device: TypeAlias = Callable[[bytes], protocol.Response]


# --- files ---


def read_file(t: Device, path: str) -> dict:
    r = t(protocol.build_get(path))
    if r.status == protocol.ST_NOT_FOUND:
        return {"error": "not_found", "path": path}
    if not r.ok:
        return {"error": protocol.STATUS_NAMES.get(r.status, "error")}
    return {"content": from_oberon(r.payload)}


def write_file(t: Device, path: str, content: str) -> dict:
    r = t(protocol.build_put(path, to_oberon(content)))
    if not r.ok:
        return {"error": protocol.STATUS_NAMES.get(r.status, "error")}
    return {"ok": True, "path": path, "bytes": len(content)}


def edit_file(t: Device, path: str, old: str, new: str) -> dict:
    got = read_file(t, path)
    if "error" in got:
        return got
    content = got["content"]
    count = content.count(old)
    if count == 0:
        return {"error": "not_found", "detail": "old string not present in file"}
    if count > 1:
        return {"error": "not_unique", "detail": f"old string occurs {count} times"}
    written = write_file(t, path, content.replace(old, new, 1))
    if "error" in written:
        return written
    return {"ok": True, "path": path, "replaced": 1}


def delete_file(t: Device, path: str) -> dict:
    log = _call_log(t, "System.DeleteFiles", path)
    if "failed" in log:
        return {"error": "not_found", "log": log.strip()}
    return {"ok": True, "path": path}


# --- listing / modules ---


def list_files(t: Device, prefix: str = "") -> dict:
    rows = _split_tabular(_call_log(t, "Puck.ListFiles", prefix))
    return {"files": [_parse_file_entry(p) for p in rows]}


def _parse_file_entry(parts: list[str]) -> dict:
    entry: dict = {"name": parts[0]}
    if len(parts) > 1 and parts[1].strip().isdigit():
        entry["size"] = int(parts[1].strip())
    if len(parts) > 2 and parts[2].strip():
        entry["date"] = parts[2].strip()
    return entry


def list_modules(t: Device) -> dict:
    rows = _split_tabular(_call_log(t, "Puck.ListModules", ""))
    return {"modules": [_parse_module_entry(p) for p in rows]}


def _parse_module_entry(parts: list[str]) -> dict:
    entry: dict = {"name": parts[0]}
    if len(parts) > 1 and parts[1].strip().lstrip("-").isdigit():
        entry["refcnt"] = int(parts[1].strip())
    if len(parts) > 2 and parts[2].strip():
        entry["code_addr"] = parts[2].strip()
    return entry


def load_module(t: Device, name: str) -> dict:
    log = _call_log(t, "Puck.Load", name).strip()
    if log.startswith("loaded"):
        return {"ok": True, "module": name}
    m = re.search(r"res=(\d+)", log)
    return {"error": "load_failed", "res": int(m.group(1)) if m else None, "log": log}


def unload_module(t: Device, name: str) -> dict:
    # EO safe-unload: System.Free /f. If no refs exist the module is fully removed;
    # if refs persist (open viewers, heap objects of its types) it is *hidden*
    # (renamed to "*<name>") so the block stays valid for live refs while a later
    # load_module allocates a fresh block. Modules.Collect (in the GC task) frees
    # hidden blocks once unreferenced. Refuses only if importing modules exist.
    log = _call_log(t, "System.Free", f"{name} /f")
    if "failed" in log:
        return {"error": "in_use", "log": log.strip()}
    return {"ok": True, "module": name, "log": log.strip()}


# --- compile / run ---


def compile_module(t: Device, name: str, new_symbol: bool = False) -> dict:
    # Returns the raw Oberon.Log delta; the model reads diagnostics / the success
    # line directly (offset->line mapping proved unnecessary — see spec.md section 7).
    par = name + ("/s" if new_symbol else "")
    r = t(protocol.build_call("ORP.Compile", to_oberon(par)))
    return {"output": from_oberon(r.payload)}


def run_command(t: Device, cmd: str, args: str = "") -> dict:
    r = t(protocol.build_call(cmd, to_oberon(args)))
    return {
        "status": protocol.STATUS_NAMES.get(r.status, str(r.status)),
        "ok": r.ok,
        "log": from_oberon(r.payload),
    }


# --- dispatch ---


_DISPATCH = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "delete_file": delete_file,
    "list_files": list_files,
    "list_modules": list_modules,
    "load_module": load_module,
    "unload_module": unload_module,
    "compile": compile_module,
    "run_command": run_command,
}


def dispatch(t: Device, name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}"}
    try:
        return fn(t, **args)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}


# --- shared internals ---


def _call_log(t: Device, cmd: str, args: str) -> str:
    return from_oberon(t(protocol.build_call(cmd, to_oberon(args))).payload)


def _split_tabular(text: str) -> list[list[str]]:
    """Tab-delimited lines split into fields, skipping blanks."""
    return [parts for parts in (line.split("\t") for line in text.splitlines()) if parts[0]]
