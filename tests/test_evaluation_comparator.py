"""Semantic result comparison tests for all supported modes."""

from __future__ import annotations

from evaluation.comparator import compare_case
from evaluation.dataset import load_cases
from evaluation.models import (
    ComparisonMode,
    Difficulty,
    EvaluationCase,
    EvaluationIntent,
    ValueNormalizer,
)


def _case(
    mode: ComparisonMode,
    columns: tuple[str, ...],
    rows: tuple[dict[str, object], ...],
    aliases: dict[str, tuple[str, ...]] | None = None,
    normalizers: dict[str, ValueNormalizer] | None = None,
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
                value_normalizers=normalizers or {},
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


def test_row_set_requires_exact_row_pairing() -> None:
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
    verdict = compare_case(
        case,
        (({"服务": "a", "数量": 2}, {"服务": "b", "数量": 1}),),
    )

    assert not verdict.matched


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


def test_single_oracle_row_cannot_be_covered_by_independent_scalar_results() -> None:
    case = _case(
        ComparisonMode.ROW_SET,
        ("请求体", "响应类型"),
        ({"请求体": "Request", "响应类型": "Response"},),
    )

    verdict = compare_case(
        case,
        (({"请求体": "Request"},), ({"响应类型": "Response"},)),
    )

    assert not verdict.matched


def test_multiple_oracle_rows_cannot_be_split_across_result_sets() -> None:
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
        (
            ({"queue": "email"}, {"queue": "food"}),
            ({"consumer": ["delivery", "mail"]},),
        ),
    )

    assert not verdict.matched


def test_row_set_requires_all_columns_in_one_result_set() -> None:
    case = _case(
        ComparisonMode.ROW_SET,
        ("queue", "consumer"),
        (
            {"queue": "email", "consumer": "mail"},
            {"queue": "food", "consumer": "delivery"},
        ),
    )

    missing = compare_case(
        case,
        (({"queue": ["email", "food"]},), ({"consumer": ["mail"]},)),
    )
    extra = compare_case(
        case,
        (
            ({"queue": ["email", "food"]},),
            ({"consumer": ["mail", "delivery", "other"]},),
        ),
    )

    assert not missing.matched
    assert not extra.matched


def test_row_set_column_fallback_still_requires_declared_aliases() -> None:
    case = _case(
        ComparisonMode.ROW_SET,
        ("queue", "consumer"),
        ({"queue": "email", "consumer": "mail"},),
    )

    verdict = compare_case(
        case,
        (({"队列": "email"},), ({"consumer": "mail"},)),
    )

    assert not verdict.matched


def test_case_verdict_exposes_full_partial_and_incorrect_outcomes() -> None:
    first = _case(
        ComparisonMode.VALUE_SET,
        ("service",),
        ({"service": "a"},),
    ).intents[0]
    second = EvaluationIntent(
        id="second",
        label="second",
        oracle_cypher="RETURN 2 AS count",
        comparison_mode=ComparisonMode.SCALAR,
        expected_columns=("count",),
        accepted_aliases={"count": ("count",)},
        expected_snapshot=({"count": 2},),
    )
    case = EvaluationCase(
        id="outcomes",
        difficulty=Difficulty.HARD,
        category="compound",
        question="compound question",
        intents=(first, second),
    )

    full = compare_case(case, (({"service": "a"},), ({"count": 2},)))
    partial = compare_case(case, (({"service": "a"},), ({"count": 1},)))
    incorrect = compare_case(case, (({"service": "b"},), ({"count": 1},)))

    assert full.semantic_outcome.value == "full"
    assert partial.semantic_outcome.value == "partial"
    assert incorrect.semantic_outcome.value == "incorrect"
    assert full.matched and not partial.matched and not incorrect.matched


def test_qualified_name_tail_normalizer_accepts_simple_member_names() -> None:
    case = _case(
        ComparisonMode.VALUE_SET,
        ("方法",),
        (
            {"方法": "example.Service.first"},
            {"方法": "example.Service.second"},
        ),
        aliases={"方法": ("方法", "声明方法")},
        normalizers={"方法": ValueNormalizer.QUALIFIED_NAME_TAIL},
    )

    verdict = compare_case(
        case,
        (({"声明方法": "second"}, {"声明方法": "first"}),),
    )

    assert verdict.matched


def test_value_normalization_is_never_enabled_implicitly() -> None:
    case = _case(
        ComparisonMode.VALUE_SET,
        ("方法",),
        ({"方法": "example.Service.first"},),
        aliases={"方法": ("方法", "声明方法")},
    )

    assert not compare_case(case, (({"声明方法": "first"},),)).matched


def test_row_set_preserves_order_inside_method_path_lists() -> None:
    case = _case(
        ComparisonMode.ROW_SET,
        ("目标方法", "方法路径"),
        (
            {
                "目标方法": "sample.Target.run",
                "方法路径": ["sample.Entry.call", "sample.Target.run"],
            },
        ),
    )

    correct = compare_case(
        case,
        (
            (
                {
                    "目标方法": "sample.Target.run",
                    "方法路径": ["sample.Entry.call", "sample.Target.run"],
                },
            ),
        ),
    )
    reversed_path = compare_case(
        case,
        (
            (
                {
                    "目标方法": "sample.Target.run",
                    "方法路径": ["sample.Target.run", "sample.Entry.call"],
                },
            ),
        ),
    )

    assert correct.matched
    assert not reversed_path.matched


def test_matching_values_do_not_override_an_unrecognized_alias() -> None:
    case = _case(
        ComparisonMode.SCALAR,
        ("API数量",),
        ({"API数量": 3},),
        aliases={"API数量": ("API数量", "接口数量")},
    )

    assert not compare_case(case, (({"调用关系数": 3},),)).matched


def test_food_delivery_extra_sender_still_fails_column_set_fallback() -> None:
    case = next(
        case for case in load_cases() if case.id == "food-delivery-full-message-chain"
    )
    rows = [dict(row) for row in case.intents[0].expected_snapshot]
    extra = dict(rows[0])
    extra["发送服务"] = "ts-delivery-service"

    verdict = compare_case(case, (tuple([*rows, extra]),))

    assert not verdict.matched


def test_notification_rest_matrix_extra_rows_still_fail_column_sets() -> None:
    case = next(
        case for case in load_cases() if case.id == "notification-senders-rest-matrix"
    )
    rows = [dict(row) for row in case.intents[0].expected_snapshot]
    extra = {
        "发送服务": "ts-admin-basic-info-service",
        "下游服务": "ts-route-service",
    }

    verdict = compare_case(case, (tuple([*rows, extra]),))

    assert not verdict.matched
