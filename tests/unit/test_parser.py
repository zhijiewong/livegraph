from livegraph.static.parser import parse_source, has_errors


def test_parse_valid_source_has_no_errors():
    tree = parse_source(b"def f():\n    return 1\n")
    assert has_errors(tree) is False
    assert tree.root_node.type == "module"


def test_parse_broken_source_reports_errors():
    tree = parse_source(b"def f(:\n")
    assert has_errors(tree) is True


def test_parse_never_raises_on_garbage():
    tree = parse_source(b"\x00\x01 not python !!!")
    assert tree.root_node is not None
