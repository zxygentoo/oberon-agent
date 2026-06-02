from fake import FakeTransport

from puck import protocol, tools, toolspec


def test_write_then_read_roundtrip():
    t = FakeTransport()
    assert tools.write_file(t, "M.Mod", "MODULE M;\nEND M.\n")["ok"]
    # stored on the device with CR line separators
    assert t.files["M.Mod"] == b"MODULE M;\rEND M.\r"
    assert tools.read_file(t, "M.Mod")["content"] == "MODULE M;\nEND M.\n"


def test_read_missing():
    assert tools.read_file(FakeTransport(), "X.Mod")["error"] == "not_found"


def test_edit_unique():
    t = FakeTransport({"M.Mod": b"a := 1;\r"})
    r = tools.edit_file(t, "M.Mod", "a := 1", "a := 2")
    assert r["ok"] and r["replaced"] == 1
    assert t.files["M.Mod"] == b"a := 2;\r"


def test_edit_not_found():
    t = FakeTransport({"M.Mod": b"x\r"})
    assert tools.edit_file(t, "M.Mod", "zzz", "q")["error"] == "not_found"


def test_edit_not_unique():
    t = FakeTransport({"M.Mod": b"a a\r"})
    assert tools.edit_file(t, "M.Mod", "a", "b")["error"] == "not_unique"


def test_delete_present_and_absent():
    t = FakeTransport({"M.Mod": b"x\r"})
    assert tools.delete_file(t, "M.Mod")["ok"]
    assert "M.Mod" not in t.files
    assert tools.delete_file(t, "Gone.Mod")["error"] == "not_found"


def test_list_files():
    t = FakeTransport({"A.Mod": b"xx", "B.Mod": b"yyy"})
    files = tools.list_files(t)["files"]
    names = {f["name"]: f for f in files}
    assert names["A.Mod"]["size"] == 2
    assert names["B.Mod"]["size"] == 3


def test_list_modules():
    mods = tools.list_modules(FakeTransport())["modules"]
    assert any(m["name"] == "Puck" for m in mods)


def test_load_module():
    assert tools.load_module(FakeTransport(), "Foo")["ok"]


def test_unload_module_hides_via_system_free_f():
    calls = []

    def call(cmd, par):
        calls.append((cmd, par.decode("latin1").replace("\r", "\n").strip()))
        return (0, b"System.Free\n removing from module list\n")

    r = tools.unload_module(FakeTransport(call=call), "Stars")
    assert r["ok"] and r["module"] == "Stars"
    assert calls == [("System.Free", "Stars /f")]


def test_compile_returns_raw_log():
    log = "  compiling M\n  pos 5 undef\ncompilation FAILED\n"
    t = FakeTransport(
        {"M.Mod": b"MODULE M;\rBEGIN x\rEND M.\r"},
        call=lambda cmd, par: (0, log.encode()) if cmd == "ORP.Compile" else None,
    )
    r = tools.compile_module(t, "M.Mod")
    assert "undef" in r["output"]
    assert "compilation FAILED" in r["output"]


def test_compile_new_symbol_passes_slash_s():
    seen = {}

    def call(cmd, par):
        if cmd == "ORP.Compile":
            seen["par"] = par.decode("latin1")
            return (0, b"  compiling M new symbol file  10 4 ABCD\n")
        return None

    t = FakeTransport({"M.Mod": b"MODULE M;\rEND M.\r"}, call=call)
    r = tools.compile_module(t, "M.Mod", new_symbol=True)
    assert seen["par"] == "M.Mod/s"
    assert "new symbol file" in r["output"]


def test_run_command_reports_status_and_log():
    t = FakeTransport(call=lambda cmd, par: (0, b"hello\n"))
    r = tools.run_command(t, "X.Y", "")
    assert r["ok"] and r["status"] == "ok" and r["log"] == "hello\n"


def test_dispatch_unknown_tool():
    assert "error" in tools.dispatch(FakeTransport(), "nope", {})


def test_dispatch_bad_args():
    assert "error" in tools.dispatch(FakeTransport(), "read_file", {"wrong": 1})


def test_dispatch_runs_compile_under_its_tool_name():
    t = FakeTransport(
        {"M.Mod": b"MODULE M;\rEND M.\r"},
        call=lambda cmd, par: (0, b"ok\n") if cmd == "ORP.Compile" else None,
    )
    r = tools.dispatch(t, "compile", {"name": "M.Mod"})
    assert r["output"] == "ok\n"


# --- failure paths and parsing edges ---


def _const_resp(status: int, payload: bytes = b""):
    """A Device that returns the same Response for every frame."""
    def proxy(_frame: bytes) -> protocol.Response:
        return protocol.Response(status, payload)
    return proxy


def test_read_file_returns_named_error_for_status_error():
    assert tools.read_file(_const_resp(protocol.ST_ERROR), "X")["error"] == "error"


def test_read_file_returns_trapped_label():
    assert tools.read_file(_const_resp(protocol.ST_TRAPPED), "X")["error"] == "trapped"


def test_write_file_propagates_status_error():
    assert tools.write_file(_const_resp(protocol.ST_ERROR), "X", "hi")["error"] == "error"


def test_edit_file_propagates_write_failure():
    """Read succeeds, the cascaded write fails: edit_file must surface the write error."""
    def proxy(frame: bytes) -> protocol.Response:
        op = frame[1]
        if op == protocol.OP_GET:
            return protocol.Response(protocol.ST_OK, b"a\r")
        if op == protocol.OP_PUT:
            return protocol.Response(protocol.ST_ERROR, b"")
        raise AssertionError(f"unexpected op {op}")

    r = tools.edit_file(proxy, "M.Mod", "a", "b")
    assert r["error"] == "error"


def test_load_module_parses_res_on_failure():
    t = FakeTransport(
        call=lambda cmd, par: (0, b"Puck.Load\n  res=2\n") if cmd == "Puck.Load" else None
    )
    r = tools.load_module(t, "Bad")
    assert r["error"] == "load_failed"
    assert r["res"] == 2


def test_load_module_failure_with_no_res_token():
    t = FakeTransport(
        call=lambda cmd, par: (0, b"nope\n") if cmd == "Puck.Load" else None
    )
    r = tools.load_module(t, "Bad")
    assert r["error"] == "load_failed"
    assert r["res"] is None


def test_unload_module_reports_in_use_on_failure_log():
    """Real System.Mod text for the importers-still-loaded path."""
    t = FakeTransport(
        call=lambda cmd, par: (
            0,
            b"System.Free\n  X unloading failed, try /f option\n  X imported by Y\n",
        )
        if cmd == "System.Free"
        else None
    )
    assert tools.unload_module(t, "X")["error"] == "in_use"


def test_list_files_passes_prefix_through():
    seen: dict[str, str] = {}

    def call(cmd, par):
        if cmd == "Puck.ListFiles":
            seen["par"] = par.decode("latin1").replace("\r", "\n").strip()
            return (0, b"")
        return None

    tools.list_files(FakeTransport(call=call), "Pu")
    assert seen["par"] == "Pu"


def test_list_files_handles_rows_without_size_or_date():
    def call(cmd, par):
        if cmd == "Puck.ListFiles":
            return (0, b"OnlyName\n")
        return None

    files = tools.list_files(FakeTransport(call=call))["files"]
    assert files == [{"name": "OnlyName"}]


def test_list_modules_parses_refcnt_and_code_addr():
    def call(cmd, par):
        if cmd == "Puck.ListModules":
            return (0, b"Puck\t1\t 00001000\nSystem\t0\t 00002000\n")
        return None

    by_name = {m["name"]: m for m in tools.list_modules(FakeTransport(call=call))["modules"]}
    assert by_name["Puck"]["refcnt"] == 1
    assert by_name["Puck"]["code_addr"] == "00001000"
    assert by_name["System"]["refcnt"] == 0


def test_list_modules_parses_negative_refcnt():
    """The parser strips a leading '-' before isdigit, so negative refcnts come through."""
    def call(cmd, par):
        if cmd == "Puck.ListModules":
            return (0, b"Stub\t-1\t 0\n")
        return None

    mods = tools.list_modules(FakeTransport(call=call))["modules"]
    assert mods[0]["refcnt"] == -1


def test_run_command_reports_trapped_status():
    t = FakeTransport(call=lambda cmd, par: (protocol.ST_TRAPPED, b"trap log\n"))
    r = tools.run_command(t, "Bad.Cmd")
    assert r["ok"] is False
    assert r["status"] == "trapped"
    assert r["log"] == "trap log\n"


def test_dispatch_routes_every_named_tool():
    """Every name in toolspec.TOOL_NAMES routes through tools._DISPATCH — no
    'unknown tool' returns for the canonical list."""
    minimal_args = {
        "read_file": {"path": "M.Mod"},
        "write_file": {"path": "M.Mod", "content": ""},
        "edit_file": {"path": "M.Mod", "old": "a", "new": "b"},
        "delete_file": {"path": "M.Mod"},
        "list_files": {},
        "list_modules": {},
        "load_module": {"name": "X"},
        "unload_module": {"name": "X"},
        "compile": {"name": "M.Mod"},
        "run_command": {"cmd": "X.Y"},
    }
    assert set(minimal_args) == toolspec.TOOL_NAMES  # sanity: this list stays in sync

    t = FakeTransport({"M.Mod": b"a\r"})
    for name, args in minimal_args.items():
        r = tools.dispatch(t, name, args)
        assert not str(r.get("error", "")).startswith("unknown tool"), f"{name} did not route"


def test_delete_file_with_failed_in_name():
    """A filename containing 'failed' must not trip the failure check — the
    discriminator is the full phrase 'deleting failed', not bare 'failed'."""
    t = FakeTransport({"failed.txt": b"x"})
    r = tools.delete_file(t, "failed.txt")
    assert r.get("ok") is True
    assert "failed.txt" not in t.files


def test_unload_module_with_failed_in_name_succeeds():
    """Same shape for unload_module: the discriminator is 'unloading failed',
    so a module name containing 'failed' (in principle possible) still succeeds."""
    seen = {}

    def call(cmd, par):
        if cmd == "System.Free":
            seen["par"] = par.decode("latin1").replace("\r", "\n").strip()
            return (0, b"System.Free\n  failedmod removing from module list\n")
        return None

    r = tools.unload_module(FakeTransport(call=call), "failedmod")
    assert r["ok"] is True
    assert seen["par"] == "failedmod /f"
