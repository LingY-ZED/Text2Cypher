"""Semantic result comparison tests for all supported modes."""

from __future__ import annotations

from evaluation.comparator import compare_case
from evaluation.models import (
    ComparisonMode,
    Difficulty,
    EvaluationCase,
    EvaluationIntent,
)


def _case(
    mode: ComparisonMode,
    columns: tuple[str, ...],
    rows: tuple[dict[str, object], ...],
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> EvaluationCase:
    return EvaluationCase(
        id="case",
        difficulty=Difficulty.SIMPLE,
        category="category",
        question="question",
        intents=(
            EvaluationIntent(
                id="intent",
                label="intent",
                oracle_cypher="RETURN 1 AS value",
                comparison_mode=mode,
                expected_columns=columns,
                accepted_aliases=aliases or {column: (column,) for column in columns},
                expected_snapshot=rows,
            ),
        ),
    )


def test_scalar_and_alias_match() -> None:
    case = _case(
        ComparisonMode.SCALAR,
        ("数量",),
        ({"数量": 3},),
        {"数量": ("数量", "总数")},
    )

    assert compare_case(case, (({"总数": 3},),)).matched is True
    assert compare_case(case, (({"总数": 4},),)).matched is False


def test_value_set_is_order_independent_but_rejects_collected_values() -> None:
    case = _case(
        ComparisonMode.VALUE_SET,
        ("服务",),
        ({"服务": "a"}, {"服务": "b"}),
    )

    assert compare_case(case, (({"服务": "b"}, {"服务": "a"}),)).matched
    assert not compare_case(case, (({"服务": ["a", "b"]},),)).matched
    assert not compare_case(case, (({"服务": "a"},),)).matched


def test_collected_set_accepts_rows_or_collect_and_rejects_extra_values() -> None:
    case = _case(
        ComparisonMode.COLLECTED_SET,
        ("服务",),
        ({"服务": "a"}, {"服务": "b"}),
    )

    assert compare_case(case, (({"服务": ["b", "a"]},),)).matched
    assert not compare_case(case, (({"服务": ["a", "b", "c"]},),)).matched


def test_row_set_ignores_order_and_extra_columns_but_preserves_pairs() -> None:
    case = _case(
        ComparisonMode.ROW_SET,
        ("服务", "数量"),
        ({"服务": "a", "数量": 1}, {"服务": "b", "数量": 2}),
    )

    assert compare_case(
        case,
        (
            (
                {"服务": "b", "数量": 2, "说明": "extra"},
                {"服务": "a", "数量": 1, "说明": "extra"},
            ),
        ),
    ).matched
    assert not compare_case(
        case,
        (({"服务": "a", "数量": 2}, {"服务": "b", "数量": 1}),),
    ).matched


def test_all_intents_must_match_across_independent_result_sets() -> None:
    first = _case(
        ComparisonMode.VALUE_SET,
        ("服务",),
        ({"服务": "a"},),
    ).intents[0]
    second = EvaluationIntent(
        id="second",
        label="second",
        oracle_cypher="RETURN 2 AS 数量",
        comparison_mode=ComparisonMode.SCALAR,
        expected_columns=("数量",),
        accepted_aliases={"数量": ("数量",)},
        expected_snapshot=({"数量": 2},),
    )
    case = EvaluationCase(
        id="compound",
        difficulty=Difficulty.HARD,
        category="compound",
        question="compound question",
        intents=(first, second),
    )

    assert compare_case(case, (({"服务": "a"},), ({"数量": 2},))).matched
    assert not compare_case(case, (({"服务": "a"},), ({"数量": 1},))).matched


def test_raw_property_column_is_normalized_to_property_name() -> None:
    case = _case(
        ComparisonMode.VALUE_SET,
        ("全限定名",),
        ({"全限定名": "package.Interface"},),
    )

    verdict = compare_case(case, (({"interface.全限定名": "package.Interface"},),))

    assert verdict.matched


def test_single_oracle_row_can_be_covered_by_independent_scalar_results() -> None:
    case = _case(
        ComparisonMode.ROW_SET,
        ("请求体", "响应类型"),
        ({"请求体": "Request", "响应类型": "Response"},),
    )

    verdict = compare_case(
        case,
        (({"请求体": "Request"},), ({"响应类型": "Response"},)),
    )

    assert verdict.matched


def test_multiple_oracle_rows_cannot_lose_pairing_across_result_sets() -> None:
    case = _case(
        ComparisonMode.ROW_SET,
        ("queue", "consumer"),
        (
            {"queue": "email", "consumer": "mail"},
            {"queue": "food", "consumer": "delivery"},
        ),
    )

    verdict = compare_case(
        case,
        (({"queue": "email"}, {"queue": "food"}), ({"consumer": "mail"},)),
    )

    assert not verdict.matched
