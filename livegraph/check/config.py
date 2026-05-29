"""TOML config loading for `livegraph check`."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or invalid."""


@dataclass(frozen=True, slots=True)
class CyclesConfig:
    enabled: bool = False
    scope: str = "module"
    provenance: str = "any"
    min_size: int = 2
    max_cycles: int = 0


@dataclass(frozen=True, slots=True)
class LayeringConfig:
    enabled: bool = False
    edge_kind: str = "any"
    max_violations: int = 0
    layers: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChurnConfig:
    enabled: bool = False
    window_days: int = 30
    hot_files_threshold: int = 10
    ignore: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class HubsConfig:
    enabled: bool = False
    kind: str = "any"
    min_fanin: int = 25
    max_hubs: int = 0


@dataclass(frozen=True, slots=True)
class CheckConfig:
    project: str
    cycles: CyclesConfig = field(default_factory=CyclesConfig)
    layering: LayeringConfig = field(default_factory=LayeringConfig)
    churn: ChurnConfig = field(default_factory=ChurnConfig)
    hubs: HubsConfig = field(default_factory=HubsConfig)
    warnings: tuple[str, ...] = field(default_factory=tuple)


_KNOWN_TOP_LEVEL = {"project", "checks"}
_KNOWN_CHECKS = {"cycles", "layering", "churn", "hubs"}

_CYCLES_KEYS = {"enabled", "scope", "provenance", "min_size", "max_cycles"}
_LAYERING_KEYS = {"enabled", "edge_kind", "max_violations", "layers"}
_CHURN_KEYS = {"enabled", "window_days", "hot_files_threshold", "ignore"}
_HUBS_KEYS = {"enabled", "kind", "min_fanin", "max_hubs"}


def load_config(path: Path) -> CheckConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed TOML in {path}: {exc}") from exc

    warnings: list[str] = []

    for top in data.keys():
        if top not in _KNOWN_TOP_LEVEL:
            warnings.append(f"unknown top-level section: [{top}]")

    project_block = data.get("project") or {}
    project_name = project_block.get("name")
    if not project_name:
        raise ConfigError("required field missing: project.name")

    checks = data.get("checks") or {}
    for ck in checks.keys():
        if ck not in _KNOWN_CHECKS:
            warnings.append(f"unknown check: [checks.{ck}]")

    def _warn_unknown(block: dict[str, Any], known: set[str],
                      section: str) -> None:
        for key in block.keys():
            if key not in known:
                warnings.append(
                    f"unknown key in [{section}]: {key}",
                )

    cycles_block = checks.get("cycles") or {}
    _warn_unknown(cycles_block, _CYCLES_KEYS, "checks.cycles")
    cycles = CyclesConfig(
        enabled=bool(cycles_block.get("enabled", False)),
        scope=str(cycles_block.get("scope", "module")),
        provenance=str(cycles_block.get("provenance", "any")),
        min_size=int(cycles_block.get("min_size", 2)),
        max_cycles=int(cycles_block.get("max_cycles", 0)),
    )

    layering_block = checks.get("layering") or {}
    _warn_unknown(layering_block, _LAYERING_KEYS, "checks.layering")
    layering = LayeringConfig(
        enabled=bool(layering_block.get("enabled", False)),
        edge_kind=str(layering_block.get("edge_kind", "any")),
        max_violations=int(layering_block.get("max_violations", 0)),
        layers=tuple(layering_block.get("layers") or ()),
    )

    churn_block = checks.get("churn") or {}
    _warn_unknown(churn_block, _CHURN_KEYS, "checks.churn")
    churn = ChurnConfig(
        enabled=bool(churn_block.get("enabled", False)),
        window_days=int(churn_block.get("window_days", 30)),
        hot_files_threshold=int(churn_block.get("hot_files_threshold", 10)),
        ignore=tuple(churn_block.get("ignore") or ()),
    )

    hubs_block = checks.get("hubs") or {}
    _warn_unknown(hubs_block, _HUBS_KEYS, "checks.hubs")
    hubs = HubsConfig(
        enabled=bool(hubs_block.get("enabled", False)),
        kind=str(hubs_block.get("kind", "any")),
        min_fanin=int(hubs_block.get("min_fanin", 25)),
        max_hubs=int(hubs_block.get("max_hubs", 0)),
    )

    return CheckConfig(
        project=project_name, cycles=cycles, layering=layering,
        churn=churn, hubs=hubs, warnings=tuple(warnings),
    )
