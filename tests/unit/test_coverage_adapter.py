from livegraph.models import Definition
from livegraph.runtime.coverage_adapter import map_coverage_to_symbols

DEFS = [
    Definition("m.py::f", "f", "function", "m.py", 1, 3, (), "..."),
    Definition("m.py::g", "g", "function", "m.py", 5, 8, (), "..."),
]


def test_attributes_lines_to_containing_definition():
    per_test = {"m.py::test_a": {("m.py", 1), ("m.py", 2)}}
    records = map_coverage_to_symbols(per_test, DEFS)
    by_symbol = {r.symbol_qn: r for r in records}
    assert by_symbol["m.py::f"].lines_covered == 2
    assert by_symbol["m.py::f"].lines_total == 3
    assert by_symbol["m.py::f"].coverage_pct == 66.67


def test_lines_outside_any_definition_are_ignored():
    per_test = {"m.py::t": {("m.py", 99)}}
    assert map_coverage_to_symbols(per_test, DEFS) == []


def test_only_emits_records_for_covered_definitions():
    per_test = {"m.py::t": {("m.py", 6)}}
    records = map_coverage_to_symbols(per_test, DEFS)
    assert [r.symbol_qn for r in records] == ["m.py::g"]
