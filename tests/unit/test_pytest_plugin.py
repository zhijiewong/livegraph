import json

from livegraph.runtime.pytest_plugin import LivegraphPlugin


class _FakeItem:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


def test_plugin_writes_observations_json(tmp_path):
    src = "def callee():\n    return 1\n\ndef caller():\n    return callee()\n"
    (tmp_path / "m.py").write_text(src)
    namespace: dict = {}
    exec(compile(src, str(tmp_path / "m.py"), "exec"), namespace)  # noqa: S102

    output = tmp_path / "obs.json"
    plugin = LivegraphPlugin(root=str(tmp_path), output_path=str(output),
                             tool_id=4, enable_coverage=False)
    plugin.start()
    item = _FakeItem("tests/m_test.py::test_caller")
    plugin.before_test(item)
    namespace["caller"]()
    plugin.after_test(item, outcome="passed", duration=0.01)
    plugin.finish()

    data = json.loads(output.read_text())
    assert any(c["caller_qn"] == "m.py::caller"
               and c["callee_qn"] == "m.py::callee"
               for c in data["runtime_calls"])
    assert data["tests"][0]["outcome"] == "passed"


def test_plugin_test_qn_uses_nodeid(tmp_path):
    plugin = LivegraphPlugin(root=str(tmp_path),
                             output_path=str(tmp_path / "o.json"),
                             tool_id=4, enable_coverage=False)
    assert plugin.test_qn(_FakeItem("tests/x.py::test_y")) == "tests/x.py::test_y"
