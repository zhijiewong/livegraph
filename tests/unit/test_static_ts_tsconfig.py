from __future__ import annotations

from livegraph.static_ts.tsconfig import TsConfig, load_tsconfig


def test_no_file_returns_empty(tmp_path):
    cfg = load_tsconfig(str(tmp_path))
    assert isinstance(cfg, TsConfig)
    assert cfg.base_url is None
    assert cfg.paths == {}


def test_baseurl_and_paths_parsed(tmp_path):
    (tmp_path / "tsconfig.json").write_text("""
{
  "compilerOptions": {
    "baseUrl": "./src",
    "paths": {
      "@/util": ["util.ts"],
      "@/calc/*": ["calc/*"]
    }
  }
}
""")
    cfg = load_tsconfig(str(tmp_path))
    assert cfg.base_url == "./src"
    assert cfg.paths == {
        "@/util": ["util.ts"],
        "@/calc/*": ["calc/*"],
    }


def test_malformed_json_returns_empty(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{ this is not json")
    cfg = load_tsconfig(str(tmp_path))
    assert cfg.base_url is None
    assert cfg.paths == {}


def test_missing_compileroptions_is_ok(tmp_path):
    (tmp_path / "tsconfig.json").write_text('{"files": ["src/index.ts"]}')
    cfg = load_tsconfig(str(tmp_path))
    assert cfg.base_url is None
    assert cfg.paths == {}


def test_resolve_alias_substitutes_pattern():
    cfg = TsConfig(
        base_url="./src",
        paths={"@/util": ["util.ts"], "@/calc/*": ["calc/*"]},
    )
    assert cfg.resolve_alias("@/util") == "src/util.ts"
    assert cfg.resolve_alias("@/calc/sum") == "src/calc/sum"
    assert cfg.resolve_alias("nothing-matches") is None
