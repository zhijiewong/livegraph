from __future__ import annotations

from livegraph.check.config import (
    CheckConfig, ChurnConfig, CyclesConfig, HubsConfig, LayeringConfig,
)
from livegraph.check.models import CheckResult, StalenessReport
from livegraph.check.runner import compute_exit_code, run_checks


class _StubBackend:
    def execute(self, cypher, **params): return []
    def verify(self): return None
    def close(self): return None


def _cfg(**overrides) -> CheckConfig:
    base = dict(
        project="p",
        cycles=CyclesConfig(enabled=False),
        layering=LayeringConfig(enabled=False),
        churn=ChurnConfig(enabled=False),
        hubs=HubsConfig(enabled=False),
    )
    base.update(overrides)
    return CheckConfig(**base)


def test_run_checks_all_disabled_returns_skipped_for_each():
    backend = _StubBackend()
    report = run_checks(
        _cfg(), backend, staleness=StalenessReport(drifted_files=0),
    )
    assert report.skipped == 4
    assert report.passed == 0
    assert report.failed == 0


def test_run_checks_passes_when_threshold_met(monkeypatch):
    monkeypatch.setattr(
        "livegraph.check.runner.check_cycles",
        lambda cfg, backend, project: CheckResult(
            check="cycles", status="passed", actual=0, threshold=0,
        ),
    )
    cfg = _cfg(cycles=CyclesConfig(enabled=True))
    report = run_checks(cfg, _StubBackend(),
                       staleness=StalenessReport(drifted_files=0))
    assert report.passed == 1
    assert report.failed == 0


def test_fail_fast_stops_after_first_failure(monkeypatch):
    calls = []
    def stub(check_name, status):
        def fn(cfg, backend, project):
            calls.append(check_name)
            return CheckResult(check=check_name, status=status,
                               actual=1 if status == "failed" else 0,
                               threshold=0)
        return fn

    monkeypatch.setattr("livegraph.check.runner.check_cycles",
                        stub("cycles", "failed"))
    monkeypatch.setattr("livegraph.check.runner.check_layering",
                        stub("layering", "passed"))
    monkeypatch.setattr("livegraph.check.runner.check_churn",
                        stub("churn", "passed"))
    monkeypatch.setattr("livegraph.check.runner.check_hubs",
                        stub("hubs", "passed"))
    cfg = _cfg(
        cycles=CyclesConfig(enabled=True),
        layering=LayeringConfig(enabled=True),
        churn=ChurnConfig(enabled=True),
        hubs=HubsConfig(enabled=True),
    )
    report = run_checks(
        cfg, _StubBackend(),
        staleness=StalenessReport(drifted_files=0),
        fail_fast=True,
    )
    assert calls == ["cycles"]
    assert report.failed == 1


def test_compute_exit_code_zero_on_all_passed():
    report_results = (
        CheckResult(check="cycles", status="passed"),
        CheckResult(check="layering", status="skipped"),
    )
    code = compute_exit_code(
        results=report_results,
        staleness=StalenessReport(drifted_files=0),
        strict=False,
    )
    assert code == 0


def test_compute_exit_code_one_on_failed():
    results = (
        CheckResult(check="cycles", status="failed", actual=1, threshold=0),
    )
    code = compute_exit_code(
        results, StalenessReport(0), strict=False,
    )
    assert code == 1


def test_compute_exit_code_two_on_strict_with_drift():
    results = (
        CheckResult(check="cycles", status="passed"),
    )
    code = compute_exit_code(
        results, StalenessReport(drifted_files=3), strict=True,
    )
    assert code == 2


def test_compute_exit_code_one_on_drift_without_strict():
    results = (
        CheckResult(check="cycles", status="passed"),
    )
    code = compute_exit_code(
        results, StalenessReport(drifted_files=3), strict=False,
    )
    assert code == 0
