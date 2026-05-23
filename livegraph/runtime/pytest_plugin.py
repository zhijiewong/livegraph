"""pytest plugin: trace the target suite and dump observations to JSON.

Runs inside the *target* project's interpreter. Configuration arrives via
two environment variables:

* ``LIVEGRAPH_ROOT``   - absolute path of the project being traced
* ``LIVEGRAPH_OUTPUT`` - path the observations JSON is written to

Only ``coverage`` must be importable in the target environment.
"""
from __future__ import annotations

import json
import os
from typing import Any

from livegraph.runtime.tracer import CallTracer

_TOOL_ID = 4


class LivegraphPlugin:
    """Drives tracing and coverage across a pytest session."""

    def __init__(self, root: str, output_path: str, tool_id: int = _TOOL_ID,
                 enable_coverage: bool = True) -> None:
        self._root = root
        self._output_path = output_path
        self._tracer = CallTracer(root=root, tool_id=tool_id)
        self._tests: list[dict[str, Any]] = []
        self._enable_coverage = enable_coverage
        self._coverage: Any = None

    @staticmethod
    def test_qn(item: Any) -> str:
        """Qualified name for a test = its pytest nodeid."""
        return str(item.nodeid)

    def start(self) -> None:
        """Begin tracing (and coverage, if enabled)."""
        if self._enable_coverage:
            import coverage

            self._coverage = coverage.Coverage(
                data_file=None, branch=False,
                config_file=False, source=[self._root],
            )
            self._coverage.start()
        self._tracer.start()

    def before_test(self, item: Any) -> None:
        """Mark the test subsequent observations belong to.

        The coverage measurement context is switched manually to the
        test's qualified name so coverage contexts line up exactly with
        the test node identities used elsewhere in the graph.
        """
        test_qn = self.test_qn(item)
        self._tracer.set_current_test(test_qn)
        if self._coverage is not None:
            self._coverage.switch_context(test_qn)

    def after_test(self, item: Any, outcome: str, duration: float) -> None:
        """Record a finished test's outcome."""
        self._tracer.set_current_test(None)
        self._tests.append({
            "qualified_name": self.test_qn(item),
            "outcome": outcome, "duration": duration,
        })

    def finish(self) -> None:
        """Stop tracing/coverage and write the observations JSON."""
        self._tracer.stop()
        coverage_payload = self._collect_coverage()
        observations = {
            "root": self._root,
            "runtime_calls": [
                {"caller_qn": caller, "callee_qn": callee,
                 "test_qn": test_qn, "observed_count": count}
                for (caller, callee, test_qn), count
                in self._tracer.counts().items()
            ],
            "tests": self._tests,
            "coverage": coverage_payload,
        }
        with open(self._output_path, "w", encoding="utf-8") as handle:
            json.dump(observations, handle, indent=2)

    def _collect_coverage(self) -> list[dict[str, Any]]:
        """Return per-test coverage as {test_qn, file, lines} dicts."""
        if self._coverage is None:
            return []
        self._coverage.stop()
        data = self._coverage.get_data()
        payload: list[dict[str, Any]] = []
        for measured_file in data.measured_files():
            rel = os.path.relpath(measured_file, self._root).replace("\\", "/")
            if rel.startswith(".."):
                continue
            contexts = data.contexts_by_lineno(measured_file)
            per_test: dict[str, list[int]] = {}
            for line, ctx_list in contexts.items():
                for ctx in ctx_list:
                    if ctx:
                        per_test.setdefault(ctx, []).append(line)
            for ctx, lines in per_test.items():
                payload.append({"test_context": ctx, "file": rel,
                                "lines": sorted(lines)})
        return payload


# -- pytest hook entry points -------------------------------------------

_PLUGIN: LivegraphPlugin | None = None


def pytest_configure(config: Any) -> None:  # pragma: no cover - needs pytest
    global _PLUGIN
    root = os.environ.get("LIVEGRAPH_ROOT")
    output = os.environ.get("LIVEGRAPH_OUTPUT")
    if not root or not output:
        return
    _PLUGIN = LivegraphPlugin(root=root, output_path=output)
    _PLUGIN.start()


def pytest_runtest_call(item: Any) -> None:  # pragma: no cover - needs pytest
    if _PLUGIN is not None:
        _PLUGIN.before_test(item)


def pytest_runtest_logreport(report: Any) -> None:  # pragma: no cover
    if _PLUGIN is not None and report.when == "call":
        _PLUGIN.after_test(report, outcome=report.outcome,
                           duration=report.duration)


def pytest_unconfigure(config: Any) -> None:  # pragma: no cover - needs pytest
    if _PLUGIN is not None:
        _PLUGIN.finish()
