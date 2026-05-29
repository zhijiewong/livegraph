from livegraph.mcp._tarjan import strongly_connected_components


def test_empty_graph_returns_no_components():
    assert strongly_connected_components({}) == []


def test_single_node_no_edges_returns_one_singleton():
    assert strongly_connected_components({"a": []}) == [["a"]]


def test_self_loop_is_a_one_node_scc():
    assert strongly_connected_components({"a": ["a"]}) == [["a"]]


def test_two_node_cycle():
    sccs = strongly_connected_components({"a": ["b"], "b": ["a"]})
    assert len(sccs) == 1
    assert sorted(sccs[0]) == ["a", "b"]


def test_three_node_cycle():
    sccs = strongly_connected_components(
        {"a": ["b"], "b": ["c"], "c": ["a"]}
    )
    assert len(sccs) == 1
    assert sorted(sccs[0]) == ["a", "b", "c"]


def test_two_disjoint_cycles():
    sccs = strongly_connected_components({
        "a": ["b"], "b": ["a"],
        "c": ["d"], "d": ["c"],
    })
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a", "b"), ("c", "d")]


def test_acyclic_returns_singletons_only():
    sccs = strongly_connected_components({
        "a": ["b"], "b": ["c"], "c": []
    })
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a",), ("b",), ("c",)]


def test_mixed_acyclic_and_cyclic():
    sccs = strongly_connected_components({
        "a": ["b"],
        "b": ["c"],
        "c": ["d", "b"],
        "d": ["e"],
        "e": [],
    })
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a",), ("b", "c"), ("d",), ("e",)]


def test_dangling_target_node_is_added_as_singleton():
    sccs = strongly_connected_components({"a": ["b"]})
    sccs_set = sorted(tuple(sorted(s)) for s in sccs)
    assert sccs_set == [("a",), ("b",)]
