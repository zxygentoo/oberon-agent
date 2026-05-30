from fake import FakeTransport

from pucxy.tools import AgentTools


def test_write_then_read_roundtrip():
    t = FakeTransport()
    tools = AgentTools(t)
    assert tools.write_file("M.Mod", "MODULE M;\nEND M.\n")["ok"]
    # stored on the device with CR line separators
    assert t.files["M.Mod"] == b"MODULE M;\rEND M.\r"
    assert tools.read_file("M.Mod")["content"] == "MODULE M;\nEND M.\n"


def test_read_missing():
    assert AgentTools(FakeTransport()).read_file("X.Mod")["error"] == "not_found"


def test_edit_unique():
    t = FakeTransport({"M.Mod": b"a := 1;\r"})
    r = AgentTools(t).edit_file("M.Mod", "a := 1", "a := 2")
    assert r["ok"] and r["replaced"] == 1
    assert t.files["M.Mod"] == b"a := 2;\r"


def test_edit_not_found():
    t = FakeTransport({"M.Mod": b"x\r"})
    assert AgentTools(t).edit_file("M.Mod", "zzz", "q")["error"] == "not_found"


def test_edit_not_unique():
    t = FakeTransport({"M.Mod": b"a a\r"})
    assert AgentTools(t).edit_file("M.Mod", "a", "b")["error"] == "not_unique"


def test_delete_present_and_absent():
    t = FakeTransport({"M.Mod": b"x\r"})
    tools = AgentTools(t)
    assert tools.delete_file("M.Mod")["ok"]
    assert "M.Mod" not in t.files
    assert tools.delete_file("Gone.Mod")["error"] == "not_found"


def test_list_files():
    t = FakeTransport({"A.Mod": b"xx", "B.Mod": b"yyy"})
    files = AgentTools(t).list_files()["files"]
    names = {f["name"]: f for f in files}
    assert names["A.Mod"]["size"] == 2
    assert names["B.Mod"]["size"] == 3


def test_list_modules():
    mods = AgentTools(FakeTransport()).list_modules()["modules"]
    assert any(m["name"] == "Agent" for m in mods)


def test_load_module():
    assert AgentTools(FakeTransport()).load_module("Foo")["ok"]


def test_unload_module_hides_via_system_free_f():
    calls = []

    def call(cmd, par):
        calls.append((cmd, par.decode("latin1").replace("\r", "\n").strip()))
        return (0, b"System.Free\n removing from module list\n")

    r = AgentTools(FakeTransport(call=call)).unload_module("Stars")
    assert r["ok"] and r["module"] == "Stars"
    assert calls == [("System.Free", "Stars /f")]


def test_compile_returns_raw_log():
    log = "  compiling M\n  pos 5 undef\ncompilation FAILED\n"
    t = FakeTransport(
        {"M.Mod": b"MODULE M;\rBEGIN x\rEND M.\r"},
        call=lambda cmd, par: (0, log.encode()) if cmd == "ORP.Compile" else None,
    )
    r = AgentTools(t).compile("M.Mod")
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
    r = AgentTools(t).compile("M.Mod", new_symbol=True)
    assert seen["par"] == "M.Mod/s"
    assert "new symbol file" in r["output"]


def test_run_command_reports_status_and_log():
    t = FakeTransport(call=lambda cmd, par: (0, b"hello\n"))
    r = AgentTools(t).run_command("X.Y", "")
    assert r["ok"] and r["status"] == "ok" and r["log"] == "hello\n"


def test_dispatch_unknown_tool():
    assert "error" in AgentTools(FakeTransport()).dispatch("nope", {})


def test_dispatch_bad_args():
    assert "error" in AgentTools(FakeTransport()).dispatch("read_file", {"wrong": 1})
