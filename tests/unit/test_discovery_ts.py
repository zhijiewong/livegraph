from __future__ import annotations

from pathlib import Path

from livegraph.discovery import discover_typescript_files


def test_finds_ts_tsx_js_jsx_mjs_cjs(tmp_path: Path):
    for name in ["a.ts", "b.tsx", "c.js", "d.jsx", "e.mjs", "f.cjs"]:
        (tmp_path / name).write_text("// x\n")
    out = sorted(discover_typescript_files(str(tmp_path)))
    assert out == ["a.ts", "b.tsx", "c.js", "d.jsx", "e.mjs", "f.cjs"]


def test_skips_node_modules_and_dist(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("// x\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.ts").write_text("// x\n")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "out.js").write_text("// x\n")
    out = sorted(discover_typescript_files(str(tmp_path)))
    assert out == ["src/a.ts"]


def test_ignores_non_ts_files(tmp_path: Path):
    (tmp_path / "a.ts").write_text("// x\n")
    (tmp_path / "README.md").write_text("hi\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    out = sorted(discover_typescript_files(str(tmp_path)))
    assert out == ["a.ts"]
