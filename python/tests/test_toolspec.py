"""Tool schema invariants: keep TOOLS in lockstep with the dispatch table and
make sure each schema is well-formed."""

from puck import tools, toolspec


def test_tool_names_match_dispatch_table():
    """Adding a tool to toolspec.TOOLS without wiring tools._DISPATCH (or vice versa)
    is a silent bug at runtime — this pins them together."""
    assert toolspec.TOOL_NAMES == set(tools._DISPATCH)


def test_each_tool_has_well_formed_openai_schema():
    for t in toolspec.TOOLS:
        assert t["type"] == "function"
        fn = t["function"]
        assert fn["name"] and isinstance(fn["name"], str)
        assert fn["description"] and isinstance(fn["description"], str)
        params = fn["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        # every name in `required` must appear in `properties`
        assert set(params["required"]) <= set(params["properties"])
