from livegraph.graph.backend import FakeBackend
from livegraph.mcp.server import bootstrap, build_server


def test_bootstrap_sets_state_and_returns_server():
    backend = FakeBackend()
    server = bootstrap(backend, project="sample")
    assert server is not None
    from livegraph.mcp import server as srv_mod
    assert srv_mod._BACKEND is backend
    assert srv_mod._PROJECT == "sample"


def test_build_server_registers_fifteen_tools_including_semantic_neighborhood():
    backend = FakeBackend()
    server = bootstrap(backend, project="sample")
    tool_names = sorted(_registered_tool_names(server))
    expected = sorted([
        "find_symbol", "get_source",
        "find_callers", "find_callees",
        "runtime_only_calls", "dead_static_calls",
        "tests_for", "untested_symbols",
        "imports", "graph_status",
        "change_impact",
        "describe_schema", "run_cypher",
        "semantic_search",
        "semantic_neighborhood",
    ])
    assert tool_names == expected


def _registered_tool_names(server) -> list[str]:
    manager = getattr(server, "_tool_manager", None) or getattr(server, "tool_manager", None)
    assert manager is not None
    tools_dict = getattr(manager, "_tools", None)
    if tools_dict is not None:
        return list(tools_dict.keys())
    # Fallback: try list_tools() sync.
    return [t.name for t in manager.list_tools()]


def test_build_server_uses_supplied_defaults_for_run_cypher():
    """build_server's default_* args should be the run_cypher tool's defaults."""
    backend = FakeBackend()
    server = bootstrap(backend, project="sample",
                       default_row_limit=42, default_timeout_seconds=7)
    manager = getattr(server, "_tool_manager", None) \
        or getattr(server, "tool_manager", None)
    tool = manager._tools["run_cypher"]
    # FastMCP exposes the registered function via the tool object's `fn`.
    fn = getattr(tool, "fn", None) or getattr(tool, "_fn", None)
    assert fn is not None, "FastMCP tool object exposes no fn attribute"
    import inspect
    sig = inspect.signature(fn)
    assert sig.parameters["row_limit"].default == 42
    assert sig.parameters["timeout_seconds"].default == 7
