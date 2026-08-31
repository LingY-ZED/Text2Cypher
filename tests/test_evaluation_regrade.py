"""Regression tests for deterministic historical evaluation regrading."""

from __future__ import annotations

from evaluation.dataset import load_cases
from evaluation.regrade import regrade_records


def test_v4_regrade_contract_produces_seventy_four_matches() -> None:
    cases = load_cases()
    records: list[dict[str, object]] = []
    index = 0
    for case in cases:
        for run in range(1, 4):
            should_match = index < 74
            sub_queries = (
                [
                    {
                        "question": case.question,
                        "cypher": intent.oracle_cypher,
                        "columns": list(intent.expected_columns),
                        "rows": [dict(row) for row in intent.expected_snapshot],
                    }
                    for intent in case.intents
                ]
                if should_match
                else [
                    {
                        "question": case.question,
                        "cypher": "RETURN 'wrong' AS value",
                        "columns": ["value"],
                        "rows": [{"value": "wrong"}],
                    }
                ]
            )
            records.append(
                {
                    "case_id": case.id,
                    "run": run,
                    "semantic_success": index < 59,
                    "intent_verdicts": [],
                    "execution_success": True,
                    "failure_stage": None if index < 59 else "semantic",
                    "sub_queries": sub_queries,
                }
            )
            index += 1

    regraded, summary = regrade_records(cases, records)

    assert len(regraded) == 120
    assert summary["old_matched"] == 59
    assert summary["new_matched"] == 74
    assert summary["remaining_failures"] == 46
    assert summary["flip_count"] == 15
    assert summary["semantic_outcomes"] == {
        "full": 74,
        "partial": 0,
        "incorrect": 46,
    }
    assert sum(bool(record["semantic_success"]) for record in regraded) == 74
    assert all("semantic_outcome" in record for record in regraded)


def test_regrade_rejects_unknown_case_ids() -> None:
    records = [
        {
            "case_id": "missing-case",
            "sub_queries": [],
            "semantic_success": False,
        }
    ]

    try:
        regrade_records(load_cases(), records)
    except ValueError as error:
        assert "unknown evaluation case" in str(error)
    else:
        raise AssertionError("unknown cases must fail regrading")
