from __future__ import annotations

from pathlib import Path

import pytest

from livegraph.check.config import (
    CheckConfig, ConfigError, load_config,
)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".livegraph.toml"
    p.write_text(text)
    return p


def test_minimal_valid_config(tmp_path):
    cfg_path = _write(tmp_path, '[project]\nname = "myproj"\n')
    cfg = load_config(cfg_path)
    assert isinstance(cfg, CheckConfig)
    assert cfg.project == "myproj"
    assert cfg.cycles.enabled is False
    assert cfg.layering.enabled is False
    assert cfg.churn.enabled is False
    assert cfg.hubs.enabled is False


def test_missing_project_raises_config_error(tmp_path):
    cfg_path = _write(tmp_path, "[checks.cycles]\nenabled = true\n")
    with pytest.raises(ConfigError, match="project.name"):
        load_config(cfg_path)


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_malformed_toml_raises_config_error(tmp_path):
    cfg_path = _write(tmp_path, "this isn't = valid = toml\n")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_cycles_check_full_parse(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.cycles]
enabled = true
scope = "module"
provenance = "any"
min_size = 2
max_cycles = 0
""")
    cfg = load_config(cfg_path)
    assert cfg.cycles.enabled is True
    assert cfg.cycles.scope == "module"
    assert cfg.cycles.provenance == "any"
    assert cfg.cycles.min_size == 2
    assert cfg.cycles.max_cycles == 0


def test_layering_layers_parsed(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.layering]
enabled = true
edge_kind = "imports"
max_violations = 0
layers = [
    { name = "web", patterns = ["web/**"] },
    { name = "domain", patterns = ["domain/**"] },
]
""")
    cfg = load_config(cfg_path)
    assert cfg.layering.enabled is True
    assert cfg.layering.edge_kind == "imports"
    assert [l["name"] for l in cfg.layering.layers] == ["web", "domain"]


def test_churn_ignore_parsed(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.churn]
enabled = true
window_days = 30
hot_files_threshold = 10
ignore = ["tests/**"]
""")
    cfg = load_config(cfg_path)
    assert cfg.churn.enabled is True
    assert cfg.churn.window_days == 30
    assert cfg.churn.hot_files_threshold == 10
    assert cfg.churn.ignore == ("tests/**",)


def test_unknown_top_level_section_warns_but_loads(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[unknown_thing]
foo = "bar"
""")
    cfg = load_config(cfg_path)
    assert cfg.project == "p"
    assert any("unknown_thing" in w for w in cfg.warnings)


def test_unknown_key_in_check_block_warns(tmp_path):
    cfg_path = _write(tmp_path, """
[project]
name = "p"

[checks.cycles]
enabled = true
mystery_key = 42
""")
    cfg = load_config(cfg_path)
    assert any("mystery_key" in w for w in cfg.warnings)
