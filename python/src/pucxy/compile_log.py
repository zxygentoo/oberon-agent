"""Parse ORP.Compile Log output into structured diagnostics. See spec.md section 4.3.

Real output (validated against bin/build-image):

    compiling Stars
    pos 59 undef
    pos 69 illegal assignment
    pos 86 not Integer
  compilation FAILED

Success ends the `compiling` line with code/data sizes and the key (and
` new symbol file` when a .smb was (re)written):

    compiling Stars new symbol file    45     8 C5386873
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DIAG = re.compile(r"^\s*pos (\d+) (.*)$")
_COMPILING = re.compile(
    r"^\s*compiling (\w+)( new symbol file)?(?:\s+(\d+)\s+(\d+)\s+([0-9A-Fa-f]+))?\s*$"
)
_FAILED = "compilation FAILED"


@dataclass
class Diagnostic:
    offset: int
    msg: str
    line: int | None = None
    col: int | None = None
    source_line: str | None = None
    context: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class CompileResult:
    ok: bool
    module: str | None = None
    symbol_file: bool = False
    code_bytes: int | None = None
    data_bytes: int | None = None
    key: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    raw: str = ""


def parse(log: str, source: str | None = None) -> CompileResult:
    res = CompileResult(ok=True, raw=log)
    if _FAILED in log:
        res.ok = False
    for line in log.splitlines():
        m = _DIAG.match(line)
        if m:
            res.ok = False
            d = Diagnostic(offset=int(m.group(1)), msg=m.group(2).rstrip())
            if source is not None:
                _locate(d, source)
            res.diagnostics.append(d)
            continue
        m = _COMPILING.match(line)
        if m:
            res.module = m.group(1)
            res.symbol_file = bool(m.group(2))
            if m.group(3) is not None:
                res.code_bytes = int(m.group(3))
                res.data_bytes = int(m.group(4))
                res.key = m.group(5).upper()
    if res.diagnostics:
        res.ok = False
    return res


def offset_to_line_col(source: str, offset: int) -> tuple[int, int]:
    """Map a byte/char offset to 1-based (line, col). CR/LF-agnostic; see spec.md section 4.3."""
    n = max(0, min(offset, len(source)))
    before = source[:n]
    line = before.count("\n") + 1
    col = n - (before.rfind("\n") + 1) + 1
    return line, col


def _locate(d: Diagnostic, source: str) -> None:
    line, col = offset_to_line_col(source, d.offset)
    lines = source.split("\n")
    d.line, d.col = line, col
    if 1 <= line <= len(lines):
        d.source_line = lines[line - 1]
    lo = max(1, line - 2)
    hi = min(len(lines), line + 2)
    d.context = [(i, lines[i - 1]) for i in range(lo, hi + 1)]
