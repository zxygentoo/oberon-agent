from pucxy import compile_log

# Real output captured from bin/build-image (see spec.md section 6). Do not hand-edit.
CAPTURED = (
    "  compiling Stars\n"
    "  pos 59 undef\n"
    "  pos 69 illegal assignment\n"
    "  pos 86 not Integer\n"
    "compilation FAILED\n"
)

# The exact broken source those offsets came from.
SOURCE = (
    "MODULE Stars;\n"
    "  VAR x: INTEGER; b: BOOLEAN;\n"
    "BEGIN\n"
    "  x := y;\n"
    "  x := b;\n"
    "  x := x * b\n"
    "END Stars.\n"
)


def test_parse_real_errors():
    r = compile_log.parse(CAPTURED, SOURCE)
    assert not r.ok
    assert r.module == "Stars"
    assert [d.msg for d in r.diagnostics] == ["undef", "illegal assignment", "not Integer"]


def test_offset_maps_to_correct_line():
    r = compile_log.parse(CAPTURED, SOURCE)
    # offset 59 is the line-terminator after '  x := y;' (line 4); the line must be exact
    d0 = r.diagnostics[0]
    assert d0.line == 4
    assert d0.source_line is not None and "y" in d0.source_line
    assert r.diagnostics[1].line == 5  # '  x := b;'
    # diagnostics carry surrounding context for the LLM
    assert any("x := y" in text for _, text in d0.context)


def test_parse_success_line():
    r = compile_log.parse("  compiling Stars new symbol file    45     8 C5386873\n", None)
    assert r.ok
    assert r.symbol_file
    assert r.module == "Stars"
    assert r.code_bytes == 45
    assert r.data_bytes == 8
    assert r.key == "C5386873"
    assert r.diagnostics == []


def test_success_without_symbol_file():
    r = compile_log.parse("  compiling Stars  6251   136 E6FCC519\n", None)
    assert r.ok
    assert not r.symbol_file
    assert r.code_bytes == 6251


def test_offset_to_line_col():
    src = "abc\ndef\nghi"
    assert compile_log.offset_to_line_col(src, 0) == (1, 1)
    assert compile_log.offset_to_line_col(src, 4) == (2, 1)
    assert compile_log.offset_to_line_col(src, 5) == (2, 2)
    assert compile_log.offset_to_line_col(src, 999) == (3, 4)  # clamped to end
