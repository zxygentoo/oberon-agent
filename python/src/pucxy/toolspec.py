"""LLM-facing tool schemas (OpenAI tools format). See spec.md section 4.3.

Explicit, named tools with structured results — deliberately not one generic verb.
"""

from __future__ import annotations


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_STR = {"type": "string"}

TOOLS = [
    _tool(
        "read_file",
        "Read a file from the Oberon system and return its text.",
        {"path": {**_STR, "description": "File name, e.g. 'Agent.Mod'."}},
        ["path"],
    ),
    _tool(
        "write_file",
        "Create or overwrite a file with the given text.",
        {"path": _STR, "content": {**_STR, "description": "Full file contents (LF newlines)."}},
        ["path", "content"],
    ),
    _tool(
        "edit_file",
        "Replace a unique occurrence of `old` with `new` in a file (str_replace).",
        {
            "path": _STR,
            "old": {**_STR, "description": "Exact text to replace; must occur exactly once."},
            "new": {**_STR, "description": "Replacement text."},
        },
        ["path", "old", "new"],
    ),
    _tool("delete_file", "Delete a file.", {"path": _STR}, ["path"]),
    _tool(
        "list_files",
        "List files, optionally restricted to a name prefix.",
        {"prefix": {**_STR, "description": "Optional name prefix; empty lists all."}},
        [],
    ),
    _tool(
        "compile",
        "Compile a module source file with ORP.Compile; returns structured diagnostics.",
        {
            "name": {**_STR, "description": "Source file, e.g. 'Agent.Mod'."},
            "new_symbol": {
                "type": "boolean",
                "description": "Pass /s to (re)write the symbol file when the interface changed.",
            },
        },
        ["name"],
    ),
    _tool(
        "load_module",
        "Load a compiled module into the running system.",
        {"name": {**_STR, "description": "Module name (no extension), e.g. 'Agent'."}},
        ["name"],
    ),
    _tool(
        "unload_module",
        "Unload a module (fails if it is still imported).",
        {"name": _STR},
        ["name"],
    ),
    _tool("list_modules", "List loaded modules with reference counts.", {}, []),
    _tool(
        "run_command",
        "Run any Oberon command 'Mod.Proc' with parameter text (escape hatch). "
        "Output is the Oberon.Log delta. Viewer-coupled commands won't work headless.",
        {
            "cmd": {**_STR, "description": "Command 'Mod.Proc'."},
            "args": {**_STR, "description": "Parameter text scanned via Oberon.Par."},
        },
        ["cmd"],
    ),
]

TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOLS)
