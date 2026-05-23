"""Invoke the target project's pytest suite under the livegraph plugin."""
from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 - required to launch the target's pytest
import sys
import tempfile
from typing import Any


class RuntimeUnavailable(RuntimeError):
    """Phase 2 cannot run (e.g. ``coverage`` missing in the target env)."""


def _coverage_importable(python: str) -> bool:
    """Return True if ``coverage`` can be imported by ``python``."""
    result = subprocess.run(  # noqa: S603
        [python, "-c", "import coverage"],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def run_pytest(root: str, python: str | None = None) -> dict[str, Any]:
    """Run the target's pytest suite traced, return parsed observations.

    ``root`` is the target project directory. ``python`` is the
    interpreter to use (defaults to the current one). The livegraph
    package must be importable by that interpreter — its directory is
    placed on PYTHONPATH for the subprocess.
    """
    python = python or sys.executable
    root = os.path.abspath(root)

    if not _coverage_importable(python):
        raise RuntimeUnavailable(
            f"`coverage` is not importable by {python}. Install it in the "
            f"target environment to enable Phase 2 (pip install coverage)."
        )

    livegraph_pkg_parent = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    with tempfile.TemporaryDirectory() as workdir:
        output_path = os.path.join(workdir, "observations.json")
        env = dict(os.environ)
        env["LIVEGRAPH_ROOT"] = root
        env["LIVEGRAPH_OUTPUT"] = output_path
        # Force coverage.py's C trace core: it supports measurement
        # contexts and leaves sys.monitoring free for the call tracer.
        env["COVERAGE_CORE"] = "ctrace"
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            livegraph_pkg_parent + os.pathsep + existing
            if existing else livegraph_pkg_parent
        )
        cmd = [python, "-m", "pytest", "-p", "livegraph.runtime.pytest_plugin",
               root]
        subprocess.run(  # noqa: S603
            cmd, env=env, cwd=root, check=False,
            capture_output=True, text=True,
        )
        if not os.path.exists(output_path):
            raise RuntimeUnavailable(
                "pytest produced no observations file — the target may have "
                "no tests, or the suite failed to start."
            )
        with open(output_path, encoding="utf-8") as handle:
            return json.load(handle)
