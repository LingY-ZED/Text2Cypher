"""Deterministic semantic comparison of Pipeline results against Oracles."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evaluation.models import ComparisonMode, EvaluationCase, EvaluationIntent


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
                values.update(_freeze(row[actual]) for row in rows if actual in row)
        if values != {_freeze(expected[expected_column])}:
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
        return _freeze(values[0]) if len(values) == 1 else ("invalid_scalar",)
    if intent.comparison_mode is ComparisonMode.VALUE_SET:
        column = mapping[intent.expected_columns[0]]
        values = tuple(row[column] for row in rows if column in row)
        if any(isinstance(value, (list, tuple, set)) for value in values):
            return ("invalid_value_set",)
        return frozenset(_freeze(value) for value in values)
    if intent.comparison_mode is ComparisonMode.COLLECTED_SET:
        column = mapping[intent.expected_columns[0]]
        return frozenset(
            _freeze(value)
            for row in rows
            if column in row
            for value in _flatten(row[column])
        )
    return frozenset(
        tuple(_freeze(row[mapping[column]]) for column in intent.expected_columns)
        for row in rows
        if all(mapping[column] in row for column in intent.expected_columns)
    )


def _flatten(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple, set)):
        return value
    return (value,)


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
