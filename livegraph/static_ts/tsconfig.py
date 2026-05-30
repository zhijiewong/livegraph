"""Minimal tsconfig.json reader: extracts baseUrl + paths aliases."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TsConfig:
    base_url: str | None = None
    paths: dict[str, list[str]] = field(default_factory=dict)

    def resolve_alias(self, specifier: str) -> str | None:
        """Try to match ``specifier`` against the configured paths.

        Returns the substituted path (joined with base_url) on a match,
        or None if no alias matches. Supports the trailing-``*`` wildcard
        form documented by tsconfig.
        """
        base = (self.base_url or ".").rstrip("/")
        if specifier in self.paths:
            target = self.paths[specifier][0]
            return _join(base, target)
        for pat, targets in self.paths.items():
            if not pat.endswith("/*"):
                continue
            prefix = pat[:-2]
            if specifier.startswith(prefix + "/"):
                rest = specifier[len(prefix) + 1:]
                target = targets[0]
                if target.endswith("/*"):
                    target = target[:-2] + "/" + rest
                return _join(base, target)
        return None


def _join(base: str, target: str) -> str:
    """Join base + target, stripping a leading ``./`` from base."""
    if base in (".", "./"):
        return target.lstrip("./")
    base = base[2:] if base.startswith("./") else base
    return f"{base}/{target}".replace("//", "/")


def load_tsconfig(project_root: str) -> TsConfig:
    """Read ``<root>/tsconfig.json``. Returns empty config on missing/bad."""
    path = os.path.join(project_root, "tsconfig.json")
    if not os.path.exists(path):
        return TsConfig()
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("malformed tsconfig.json: %s", exc)
        return TsConfig()

    co = data.get("compilerOptions") or {}
    base_url = co.get("baseUrl")
    paths = co.get("paths") or {}
    if not isinstance(paths, dict):
        paths = {}
    return TsConfig(base_url=base_url, paths=paths)
