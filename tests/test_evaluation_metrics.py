"""Tests for official metrics, latency, stability, and recovery gates."""

from __future__ import annotations

from evaluation.metrics import calculate_metrics


def _record(index: int, *, semantic: bool = True) -> dict[str, object]:
    return {
        "case_id": f"case-{index % 30}",
        "difficulty": ("simple", "medium", "hard")[index % 3],
        "category": f"category-{index % 5}",
        "duration_seconds": float(index + 1),
        "generation_success": True,
        "syntax_success": True,
        "execution_success": True,
        "semantic_success": semantic,
        "semantic_outcome": "full" if semantic else "incorrect",
        "intent_verdicts": [
            {"intent_id": "intent", "matched": semantic, "reason": "result"}
        ],
        "initial_syntax_success": True,
        "initial_execution_success": True,
        "nonempty_success": True,
        "recovery_events": [],
    }


def test_calculates_ninety_sample_rates_nearest_rank_and_stability() -> None:
    records = [_record(index, semantic=index < 77) for index in range(90)]
    metrics = calculate_metrics(
        records,
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )

    assert metrics["sample_count"] == 90
    assert metrics["official"]["query_accuracy"]["count"] == 77
    assert metrics["official"]["query_accuracy"]["passed"] is True
    assert metrics["official"]["average_response_seconds"] == 45.5
    assert metrics["official"]["p95_response_seconds"]["value"] == 86.0
    assert metrics["quality_gate_passed"] is False
    assert metrics["diagnostics"]["stability"]["cases"]["case-0"] == {
        "passed": 3,
        "runs": 3,
        "stable": True,
    }


def test_zero_natural_recovery_is_na_and_not_a_false_success() -> None:
    metrics = calculate_metrics(
        [_record(0)],
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )

    recovery = metrics["official"]["error_recovery_rate"]
    assert recovery["value"] is None
    assert recovery["passed"] is None
    assert metrics["quality_gate_passed"] is True


def test_decomposition_contract_is_a_hard_gate_with_legacy_default() -> None:
    record = _record(0)
    metrics = calculate_metrics(
        [record],
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )
    assert metrics["official"]["decomposition_contract"]["passed"] is True

    record["decomposition_contract_success"] = False
    metrics = calculate_metrics(
        [record],
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )
    assert metrics["official"]["decomposition_contract"]["passed"] is False
    assert metrics["quality_gate_passed"] is False


def test_reviewer_calls_and_structured_verdicts_are_counted_separately() -> None:
    accepted = _record(0)
    accepted["events"] = [
        {"component": "llm", "stage": "reviewer", "outcome": "succeeded"},
        {
            "component": "decomposer",
            "stage": "review",
            "outcome": "accepted",
            "reason": "VALID",
        },
    ]
    missing = _record(1)
    missing["events"] = [
        {"component": "llm", "stage": "reviewer", "outcome": "succeeded"}
    ]

    metrics = calculate_metrics(
        [accepted, missing],
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )

    assert metrics["diagnostics"]["decomposition_review"] == {
        "calls": 2,
        "verdicts": 1,
        "missing_verdicts": 1,
        "consistent": False,
        "outcomes": {"accepted": 1},
        "reasons": {"VALID": 1},
    }


def test_summary_calls_and_template_fallbacks_are_counted_separately() -> None:
    llm_generated = _record(0)
    llm_generated["events"] = [
        {"component": "llm", "stage": "summarizer", "outcome": "succeeded"},
        {
            "component": "result_summarizer",
            "stage": "summary",
            "outcome": "generated",
            "mode": "llm",
            "reason": None,
        },
    ]
    no_model_fallback = _record(1)
    no_model_fallback["events"] = [
        {
            "component": "result_summarizer",
            "stage": "summary",
            "outcome": "fallback",
            "mode": "template",
            "reason": "empty_result",
        }
    ]

    metrics = calculate_metrics(
        [llm_generated, no_model_fallback],
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )

    assert metrics["diagnostics"]["result_summary"] == {
        "calls": 1,
        "summaries": 2,
        "llm_generated": 1,
        "fallbacks": 1,
        "fallback_rate": {"count": 1, "total": 2, "value": 0.5},
        "missing_events": 0,
        "consistent": True,
        "outcomes": {"fallback": 1, "generated": 1},
        "modes": {"llm": 1, "template": 1},
        "reasons": {"empty_result": 1},
    }


def test_natural_recovery_and_transport_retries_are_counted_separately() -> None:
    record = _record(0)
    record["recovery_events"] = [
        {"event": "cypher_correction_started", "reason": "validation"},
        {"event": "cypher_correction_succeeded", "reason": "validation"},
        {"event": "retry_scheduled", "reason": "http_503"},
        {"event": "retry_succeeded", "reason": "http_503"},
    ]

    metrics = calculate_metrics(
        [record],
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )

    assert metrics["official"]["error_recovery_rate"]["value"] == 1.0
    assert metrics["diagnostics"]["correction_reasons"] == {"validation": 1}
    assert metrics["diagnostics"]["transport_retries"] == {
        "scheduled": 1,
        "recovered": 1,
        "exhausted": 0,
    }


def test_three_level_outcomes_and_intent_coverage_are_diagnostic_only() -> None:
    full = _record(0)
    full["intent_verdicts"] = [
        {"intent_id": "a", "matched": True},
        {"intent_id": "b", "matched": True},
    ]
    partial = _record(1, semantic=False)
    partial["semantic_outcome"] = "partial"
    partial["intent_verdicts"] = [
        {"intent_id": "a", "matched": True},
        {"intent_id": "b", "matched": False},
    ]
    incorrect = _record(2, semantic=False)
    incorrect["intent_verdicts"] = [
        {"intent_id": "a", "matched": False},
        {"intent_id": "b", "matched": False},
    ]

    metrics = calculate_metrics(
        [full, partial, incorrect],
        {"parse": True, "validation": True, "execution": True, "empty": True},
    )

    assert metrics["official"]["query_accuracy"]["count"] == 1
    assert metrics["diagnostics"]["semantic_outcomes"] == {
        "full": {"count": 1, "total": 3, "value": 0.333333},
        "partial": {"count": 1, "total": 3, "value": 0.333333},
        "incorrect": {"count": 1, "total": 3, "value": 0.333333},
    }
    assert metrics["diagnostics"]["intent_coverage"] == {
        "count": 3,
        "total": 6,
        "value": 0.5,
    }
    assert metrics["diagnostics"]["by_difficulty"]["medium"][
        "partial_rate"
    ] == 1.0
