from __future__ import annotations

from pathlib import Path
from typing import Any

from livegraph.ingest import ingest_project


class _RecordingBackend:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any):
        self.calls.append((cypher, params))
        return []

    def verify(self): return None
    def close(self): return None


def _make_mixed_project(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "calc.py").write_text(
        "def py_add(a, b):\n    return a + b\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.ts").write_text(
        "export function tsNormalize(x: string): string { return x.trim(); }\n"
    )
    return tmp_path


def _ingested_paths(backend) -> set[str]:
    file_calls = [c for c in backend.calls if "MERGE (f:File" in c[0]]
    paths = set()
    for c in file_calls:
        for row in (c[1].get("rows") or []):
            paths.add(row.get("path"))
    return paths


def test_auto_dispatch_runs_both_pipelines(tmp_path: Path):
    project = _make_mixed_project(tmp_path)
    backend = _RecordingBackend()
    summary = ingest_project(str(project), backend, project_name="mixed")
    paths = _ingested_paths(backend)
    assert "pkg/calc.py" in paths
    assert "src/util.ts" in paths
    assert summary.files >= 3


def test_lang_python_skips_ts(tmp_path: Path):
    project = _make_mixed_project(tmp_path)
    backend = _RecordingBackend()
    ingest_project(
        str(project), backend, project_name="mixed", lang="python",
    )
    paths = _ingested_paths(backend)
    assert "pkg/calc.py" in paths
    assert "src/util.ts" not in paths


def test_lang_typescript_skips_python(tmp_path: Path):
    project = _make_mixed_project(tmp_path)
    backend = _RecordingBackend()
    ingest_project(
        str(project), backend, project_name="mixed", lang="typescript",
    )
    paths = _ingested_paths(backend)
    assert "src/util.ts" in paths
    assert "pkg/calc.py" not in paths
