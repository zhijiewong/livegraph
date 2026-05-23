from livegraph.mcp.tools import _symbol_from_row, _kind_from_labels


def test_kind_from_labels_picks_first_known_label():
    assert _kind_from_labels(["Function"]) == "function"
    assert _kind_from_labels(["Function", "Test"]) == "function"
    assert _kind_from_labels(["Method"]) == "method"
    assert _kind_from_labels(["Class"]) == "class"


def test_kind_from_labels_unknown_returns_none():
    assert _kind_from_labels(["UnknownLabel"]) is None
    assert _kind_from_labels([]) is None


def test_symbol_from_row_maps_canonical_fields():
    row = {
        "qualified_name": "a.py::f", "name": "f", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 3,
    }
    assert _symbol_from_row(row) == {
        "qualified_name": "a.py::f", "name": "f", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 3,
    }
