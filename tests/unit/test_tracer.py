from livegraph.runtime.tracer import CallTracer


def test_tracer_records_project_calls(tmp_path):
    src = (
        "def callee():\n    return 1\n\n"
        "def caller():\n    return callee()\n"
    )
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102

    tracer = CallTracer(root=str(tmp_path), tool_id=3)
    tracer.start()
    tracer.set_current_test("m.py::test_it")
    try:
        namespace["caller"]()
    finally:
        tracer.stop()

    calls = tracer.runtime_calls()
    pairs = {(c.caller_qn, c.callee_qn) for c in calls}
    assert ("m.py::caller", "m.py::callee") in pairs
    assert all(c.test_qn == "m.py::test_it" for c in calls)


def test_tracer_ignores_calls_outside_project(tmp_path):
    src = "def caller():\n    return len([1, 2, 3])\n"
    path = tmp_path / "m.py"
    path.write_text(src)
    namespace: dict = {}
    exec(compile(src, str(path), "exec"), namespace)  # noqa: S102

    tracer = CallTracer(root=str(tmp_path), tool_id=3)
    tracer.start()
    tracer.set_current_test("m.py::t")
    try:
        namespace["caller"]()
    finally:
        tracer.stop()
    assert tracer.runtime_calls() == []
