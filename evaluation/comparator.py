"""Deterministic semantic comparison of Pipeline results against Oracles."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evaluation.models import (
    ComparisonMode,
    EvaluationCase,
    EvaluationIntent,
    SemanticOutcome,
    ValueNormalizer,
)


@dataclass(frozen=True, slots=True)
class IntentVerdict:
    """Comparison outcome for one Oracle intent."""

    intent_id: str
    matched: bool
    reason: str


@dataclass(frozen=True, slots=True)
class CaseVerdict:
    """All intent outcomes for one case execution."""

    matched: bool
    intents: tuple[IntentVerdict, ...]
    semantic_outcome: SemanticOutcome = field(init=False)

    def __post_init__(self) -> None:
        matched_count = sum(verdict.matched for verdict in self.intents)
        if matched_count == len(self.intents) and self.intents:
            outcome = SemanticOutcome.FULL
        elif matched_count:
            outcome = SemanticOutcome.PARTIAL
        else:
            outcome = SemanticOutcome.INCORRECT
        if self.matched is not (outcome is SemanticOutcome.FULL):
            raise ValueError("matched must agree with the intent verdicts")
        object.__setattr__(self, "semantic_outcome", outcome)


def compare_case(
    case: EvaluationCase,
    result_sets: Sequence[Sequence[Mapping[str, Any]]],
) -> CaseVerdict:
    """Require every independent intent to match a compatible result projection."""

    verdicts = tuple(_compare_intent(intent, result_sets) for intent in case.intents)
    return CaseVerdict(
        matched=all(verdict.matched for verdict in verdicts),
        intents=verdicts,
    )


def _compare_intent(
    intent: EvaluationIntent,
    result_sets: Sequence[Sequence[Mapping[str, Any]]],
) -> IntentVerdict:
    expected = _canonical_expected(intent)
    candidates = tuple(
        projection
        for rows in result_sets
        if (projection := _project(intent, rows)) is not None
    )
    if expected in candidates:
        return IntentVerdict(intent.id, True, "结果与 Oracle 精确一致")
    if _matches_singleton_fragments(intent, result_sets):
        return IntentVerdict(intent.id, True, "多个结果共同覆盖单行 Oracle")
    if _matches_column_sets(intent, result_sets):
        return IntentVerdict(
            intent.id,
            True,
            "各语义列集合与 Oracle 一致（忽略行内配对）",
        )
    if not candidates:
        return IntentVerdict(intent.id, False, "未找到包含所需语义列的结果")
    return IntentVerdict(
        intent.id,
        False,
        "结果列存在，但值与 Oracle 不一致",
    )


def _canonical_expected(intent: EvaluationIntent) -> object:
    rows = tuple(dict(row) for row in intent.expected_snapshot)
    return _canonical_projection(
        intent, rows, {column: column for column in intent.expected_columns}
    )


def _project(
    intent: EvaluationIntent,
    rows: Sequence[Mapping[str, Any]],
) -> object | None:
    available = sorted({str(column) for row in rows for column in row})
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for expected_column in intent.expected_columns:
        actual = _find_column(
            available,
            intent.accepted_aliases[expected_column],
            used,
        )
        if actual is None:
            return None
        mapping[expected_column] = actual
        used.add(actual)
    return _canonical_projection(intent, rows, mapping)


def _find_column(
    available: Sequence[str],
    aliases: Sequence[str],
    used: set[str],
) -> str | None:
    alias_set = set(aliases)
    return next(
        (
            column
            for column in available
            if column not in used
            and (column in alias_set or _property_name(column) in alias_set)
        ),
        None,
    )


def _property_name(column: str) -> str:
    """Normalize Neo4j's raw ``variable.property`` result-column form."""

    candidate = column.strip()
    if "." not in candidate or any(token in candidate for token in " ()[]{}"):
        return candidate.strip("`")
    return candidate.rsplit(".", maxsplit=1)[-1].strip("`")


def _matches_singleton_fragments(
    intent: EvaluationIntent,
    result_sets: Sequence[Sequence[Mapping[str, Any]]],
) -> bool:
    """Allow one Oracle row to be returned as independent scalar projections."""

    if len(intent.expected_snapshot) != 1 or len(intent.expected_columns) < 2:
        return False
    expected = intent.expected_snapshot[0]
    for expected_column in intent.expected_columns:
        values: set[object] = set()
        for rows in result_sets:
            available = sorted({str(column) for row in rows for column in row})
            actual = _find_column(
                available,
                intent.accepted_aliases[expected_column],
                set(),
            )
            if actual is not None:
                values.update(
                    _canonical_value(intent, expected_column, row[actual])
                    for row in rows
                    if actual in row
                )
        if values != {
            _canonical_value(intent, expected_column, expected[expected_column])
        }:
            return False
    return True


def _matches_column_sets(
    intent: EvaluationIntent,
    result_sets: Sequence[Sequence[Mapping[str, Any]]],
) -> bool:
    """Compare every ROW_SET column independently after exact rows fail.

    This deliberately ignores row-level pairing while retaining exact set equality
    for every explicitly aliased and normalized semantic column.
    """

    if (
        intent.comparison_mode is not ComparisonMode.ROW_SET
        or len(intent.expected_columns) < 2
    ):
        return False
    for expected_column in intent.expected_columns:
        expected_values = {
            _canonical_value(intent, expected_column, value)
            for row in intent.expected_snapshot
            for value in _flatten(row[expected_column])
        }
        actual_values: set[object] = set()
        found = False
        for rows in result_sets:
            available = sorted({str(column) for row in rows for column in row})
            actual_column = _find_column(
                available,
                intent.accepted_aliases[expected_column],
                set(),
            )
            if actual_column is None:
                continue
            found = True
            actual_values.update(
                _canonical_value(intent, expected_column, value)
                for row in rows
                if actual_column in row
                for value in _flatten(row[actual_column])
            )
        if not found or actual_values != expected_values:
            return False
    return True


def _canonical_projection(
    intent: EvaluationIntent,
    rows: Sequence[Mapping[str, Any]],
    mapping: Mapping[str, str],
) -> object:
    if intent.comparison_mode is ComparisonMode.SCALAR:
        column = mapping[intent.expected_columns[0]]
        values = tuple(row[column] for row in rows if column in row)
        return (
            _canonical_value(intent, intent.expected_columns[0], values[0])
            if len(values) == 1
            else ("invalid_scalar",)
        )
    if intent.comparison_mode is ComparisonMode.VALUE_SET:
        column = mapping[intent.expected_columns[0]]
        values = tuple(row[column] for row in rows if column in row)
        if any(isinstance(value, (list, tuple, set)) for value in values):
            return ("invalid_value_set",)
        return frozenset(
            _canonical_value(intent, intent.expected_columns[0], value)
            for value in values
        )
    if intent.comparison_mode is ComparisonMode.COLLECTED_SET:
        column = mapping[intent.expected_columns[0]]
        return frozenset(
            _canonical_value(intent, intent.expected_columns[0], value)
            for row in rows
            if column in row
            for value in _flatten(row[column])
        )
    return frozenset(
        tuple(
            _canonical_value(intent, column, row[mapping[column]])
            for column in intent.expected_columns
        )
        for row in rows
        if all(mapping[column] in row for column in intent.expected_columns)
    )


def _flatten(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple, set)):
        return value
    return (value,)


def _canonical_value(
    intent: EvaluationIntent,
    expected_column: str,
    value: Any,
) -> object:
    normalizer = intent.value_normalizers[expected_column]
    if normalizer is ValueNormalizer.QUALIFIED_NAME_TAIL and isinstance(value, str):
        value = value.rsplit(".", maxsplit=1)[-1]
    return _freeze(value)


def _freeze(value: Any) -> object:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return value
