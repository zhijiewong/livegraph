# tests/integration/test_mcp_server_smoke.py
"""Round-trip the MCP protocol against livegraph's FastMCP server."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration


def test_mcp_server_lists_and_calls_tools_over_protocol(ingested_sample):
    """End-to-end: register tools, list them, call graph_status."""
    backend, project = ingested_sample
    from livegraph.mcp.server import bootstrap

    async def run() -> None:
        server = bootstrap(backend, project)
        manager = getattr(server, "_tool_manager", None) \
            or getattr(server, "tool_manager", None)
        assert manager is not None
        # Get tool names from the internal _tools dict (sync access).
        tools_dict = getattr(manager, "_tools", None)
        if tools_dict is not None:
            tool_names = sorted(tools_dict.keys())
        else:
            # Fallback if SDK changes — assume async list_tools().
            listed = manager.list_tools()
            if asyncio.iscoroutine(listed):
                listed = await listed
            tool_names = sorted(t.name for t in listed)
        assert tool_names == sorted([
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
        # Invoke graph_status through the manager (same code path FastMCP
        # uses when an MCP client calls a tool over stdio).
        result = await manager.call_tool("graph_status", {})
        # FastMCP returns a CallToolResult-like object; depending on SDK
        # version it has `.content` (text-content list), `.structuredContent`,
        # or returns a tuple/dict.
        payload = _extract_payload(result)
        assert payload["project"] == project
        assert payload["files"] == 3
        assert payload["calls_runtime_only"] >= 1

    asyncio.run(run())


def _extract_payload(result):
    """Best-effort unwrap of FastMCP's call_tool return value across versions."""
    import json
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple) and result:
        # Some FastMCP versions return (content, structured) from call_tool.
        if isinstance(result[-1], dict):
            return result[-1]
        if isinstance(result[0], dict):
            return result[0]
        result = result[0]
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    content = getattr(result, "content", None)
    if content:
        first = content[0]
        text = getattr(first, "text", None) or str(first)
        return json.loads(text)
    raise AssertionError(f"Cannot extract payload from {type(result)!r}: {result!r}")
