from livegraph.graph.backend import FakeBackend
from livegraph.mcp.server import bootstrap, build_server


def test_bootstrap_sets_state_and_returns_server():
    backend = FakeBackend()
    server = bootstrap(backend, project="sample")
    assert server is not None
    from livegraph.mcp import server as srv_mod
    assert srv_mod._BACKEND is backend
    assert srv_mod._PROJECT == "sample"


def test_build_server_registers_thirteen_tools_including_describe_and_run():
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
