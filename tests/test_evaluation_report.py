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
        "events": [
            {
                "component": "llm",
                "stage": "primary_agent",
                "outcome": "succeeded",
            },
            {
                "component": "llm",
                "stage": "summarizer",
                "outcome": "succeeded",
            },
            {
                "component": "result_summarizer",
                "stage": "summary",
                "outcome": "generated",
                "mode": "llm",
                "reason": None,
            },
            {
                "component": "primary_agent",
                "stage": "planning",
                "outcome": "planned",
                "query_count": 1,
                "decomposed": False,
                "reason": None,
            },
            {
                "component": "primary_agent",
                "stage": "plan_result",
                "outcome": "succeeded",
                "query_count": 1,
                "decomposed": False,
                "queries": [{"question": "question"}],
            },
        ],
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
    assert "Primary Agent LLM 调用：1" in report
    assert "结构化计划：1" in report
    assert "Primary Agent 观测一致性：一致" in report
    assert "计划回退：0" in report
    assert "总结 LLM 调用：1" in report
    assert "总结事件：1" in report
    assert "LLM 总结成功：1" in report
    assert "模板降级：0.0%（0/1）" in report
    assert "总结观测一致性：一致" in report
    assert "## 单轮案例结果" in report
    assert "三轮稳定性" not in report
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
