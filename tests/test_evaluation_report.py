"""Report and PNG charts must derive from the same metric payload."""

from __future__ import annotations

from pathlib import Path

from evaluation.metrics import calculate_metrics
from evaluation.report import render_report


def test_report_contains_metrics_and_five_valid_png_charts(tmp_path: Path) -> None:
    record = {
        "case_id": "case",
        "difficulty": "simple",
        "category": "lookup",
        "question": "question",
        "run": 1,
        "duration_seconds": 1.0,
        "generation_success": True,
        "syntax_success": True,
        "execution_success": True,
        "nonempty_success": True,
        "semantic_success": True,
        "semantic_outcome": "full",
        "intent_verdicts": [
            {"intent_id": "intent", "matched": True, "reason": "matched"}
        ],
        "failure_stage": None,
        "error": None,
        "sub_queries": [],
        "initial_syntax_success": True,
        "initial_execution_success": True,
        "recovery_events": [],
    }
    probes = {
        "parse": True,
        "validation": True,
        "execution": True,
        "empty_result": True,
    }
    metrics = calculate_metrics([record], probes)
    metadata = {
        "revision": "error-recovery",
        "revision_sha": "abc",
        "model": "model",
        "runs_per_case": 1,
        "schema_fingerprint": "fingerprint",
    }

    render_report(tmp_path, metadata, [record], metrics, probes)

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Cypher 生成率" in report
    assert "三级语义结果" in report
    assert "Intent 覆盖率" in report
    assert "100.0%" in report
    for name in (
        "quality-gates.png",
        "semantic-outcomes.png",
        "difficulty-and-category.png",
        "latency-distribution.png",
        "recovery-outcomes.png",
    ):
        content = (tmp_path / "charts" / name).read_bytes()
        assert content.startswith(b"\x89PNG\r\n\x1a\n")
