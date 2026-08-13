"""Decomposition contracts derive from the observed planner event."""

from __future__ import annotations

from evaluation.models import (
    ComparisonMode,
    DecompositionContract,
    Difficulty,
    EvaluationCase,
    EvaluationIntent,
)
from evaluation.run import _decomposition_contract_passed


def _case(contract: DecompositionContract) -> EvaluationCase:
    return EvaluationCase(
        id="case",
        difficulty=Difficulty.HARD,
        category="compound",
        question="question",
        intents=(
            EvaluationIntent(
                id="intent",
                label="intent",
                oracle_cypher="RETURN 1 AS value",
                comparison_mode=ComparisonMode.SCALAR,
                expected_columns=("value",),
                accepted_aliases={"value": ("value",)},
                expected_snapshot=({"value": 1},),
            ),
        ),
        decomposition_contract=contract,
    )


def _event(*, decomposed: bool, count: int) -> list[dict[str, object]]:
    return [
        {
            "component": "decomposer",
            "stage": "decomposition",
            "outcome": "succeeded",
            "decomposed": decomposed,
            "sub_question_count": count,
        }
    ]


def test_contract_uses_observed_decomposition_without_pipeline_response() -> None:
    assert _decomposition_contract_passed(
        _case(DecompositionContract.MUST_SPLIT),
        _event(decomposed=True, count=3),
    )
    assert _decomposition_contract_passed(
        _case(DecompositionContract.MUST_PRESERVE),
        _event(decomposed=False, count=1),
    )


def test_contract_rejects_missing_or_inconsistent_observation() -> None:
    split = _case(DecompositionContract.MUST_SPLIT)

    assert not _decomposition_contract_passed(split, [])
    assert not _decomposition_contract_passed(
        split,
        _event(decomposed=False, count=1),
    )
    assert _decomposition_contract_passed(
        _case(DecompositionContract.ANY),
        [],
    )
