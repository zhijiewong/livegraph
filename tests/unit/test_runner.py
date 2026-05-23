import json

import pytest

from livegraph.runtime.runner import run_pytest, RuntimeUnavailable


def test_run_pytest_returns_parsed_observations(tmp_path, monkeypatch):
    obs = {"root": str(tmp_path), "runtime_calls": [], "tests": [],
           "coverage": []}

    def fake_run(cmd, env, cwd, check, capture_output, text):
        output = env["LIVEGRAPH_OUTPUT"]
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(obs, handle)
        class _R:
            returncode = 0
            stdout = "1 passed"
            stderr = ""
        return _R()

    monkeypatch.setattr("livegraph.runtime.runner.subprocess.run", fake_run)
    monkeypatch.setattr("livegraph.runtime.runner._coverage_importable",
                        lambda python: True)
    result = run_pytest(str(tmp_path), python="python")
    assert result["root"] == str(tmp_path)
    assert result["tests"] == []


def test_run_pytest_raises_when_coverage_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("livegraph.runtime.runner._coverage_importable",
                        lambda python: False)
    with pytest.raises(RuntimeUnavailable, match="coverage"):
        run_pytest(str(tmp_path), python="python")
