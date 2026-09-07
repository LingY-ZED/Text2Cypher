"""Strict, immutable models for the versioned evaluation dataset."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


class Difficulty(StrEnum):
    """The three fixed difficulty bands in the four-week plan."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    HARD = "hard"


class ComparisonMode(StrEnum):
    """Supported deterministic Oracle comparison strategies."""

    SCALAR = "scalar"
    VALUE_SET = "value_set"
    ROW_SET = "row_set"
    COLLECTED_SET = "collected_set"


class ValueNormalizer(StrEnum):
    """Explicit value normalization allowed by an evaluation intent."""

    IDENTITY = "identity"
    QUALIFIED_NAME_TAIL = "qualified_name_tail"


class SemanticOutcome(StrEnum):
    """Three-level semantic outcome for one evaluated question run."""

    FULL = "full"
    PARTIAL = "partial"
    INCORRECT = "incorrect"


class DecompositionContract(StrEnum):
    """Expected decomposition behavior for an evaluation case."""

    ANY = "any"
    MUST_PRESERVE = "must_preserve"
    MUST_SPLIT = "must_split"


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class EvaluationIntent:
    """One independently verifiable semantic intent in a question."""

    id: str
    label: str
    oracle_cypher: str
    comparison_mode: ComparisonMode
    expected_columns: tuple[str, ...]
    accepted_aliases: Mapping[str, tuple[str, ...]]
    expected_snapshot: tuple[Mapping[str, Any], ...]
    value_normalizers: Mapping[str, ValueNormalizer] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "intent id"))
        object.__setattr__(self, "label", _text(self.label, "intent label"))
        cypher = _text(self.oracle_cypher, "oracle_cypher")
        if ";" in cypher:
            raise ValueError("oracle_cypher must contain exactly one statement")
        object.__setattr__(self, "oracle_cypher", cypher)
        columns = tuple(
            _text(value, "expected column") for value in self.expected_columns
        )
        if not columns or len(set(columns)) != len(columns):
            raise ValueError("expected_columns must be non-empty and unique")
        object.__setattr__(self, "expected_columns", columns)
        aliases: dict[str, tuple[str, ...]] = {}
        for column in columns:
            values = tuple(
                dict.fromkeys(
                    _text(value, f"alias for {column}")
                    for value in self.accepted_aliases.get(column, ())
                )
            )
            if column not in values:
                values = (column, *values)
            aliases[column] = values
        if set(self.accepted_aliases) != set(columns):
            raise ValueError("accepted_aliases must cover exactly expected_columns")
        object.__setattr__(self, "accepted_aliases", MappingProxyType(aliases))
        unknown_normalizers = set(self.value_normalizers) - set(columns)
        if unknown_normalizers:
            raise ValueError("value_normalizers may only reference expected_columns")
        normalizers: dict[str, ValueNormalizer] = {}
        for column in columns:
            try:
                normalizers[column] = ValueNormalizer(
                    self.value_normalizers.get(column, ValueNormalizer.IDENTITY)
                )
            except ValueError as error:
                raise ValueError(
                    f"value normalizer for {column} is invalid"
                ) from error
        object.__setattr__(
            self,
            "value_normalizers",
            MappingProxyType(normalizers),
        )
        snapshot = tuple(MappingProxyType(dict(row)) for row in self.expected_snapshot)
        if not snapshot and self.comparison_mode is not ComparisonMode.ROW_SET:
            raise ValueError("only row_set intents may use an empty expected_snapshot")
        if any(not set(columns) <= set(row) for row in snapshot):
            raise ValueError("every snapshot row must contain all expected columns")
        object.__setattr__(self, "expected_snapshot", snapshot)
        if self.comparison_mode is ComparisonMode.SCALAR and (
            len(columns) != 1 or len(snapshot) != 1
        ):
            raise ValueError("scalar intents require one column and one row")
        if (
            self.comparison_mode
            in {
                ComparisonMode.VALUE_SET,
                ComparisonMode.COLLECTED_SET,
            }
            and len(columns) != 1
        ):
            raise ValueError("set intents require exactly one expected column")


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """A golden natural-language question with one or more Oracle intents."""

    id: str
    difficulty: Difficulty
    category: str
    question: str
    intents: tuple[EvaluationIntent, ...]
    decomposition_contract: DecompositionContract = DecompositionContract.ANY

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "case id"))
        object.__setattr__(self, "category", _text(self.category, "category"))
        object.__setattr__(self, "question", _text(self.question, "question"))
        try:
            contract = DecompositionContract(self.decomposition_contract)
        except ValueError as error:
            raise ValueError("decomposition_contract is invalid") from error
        object.__setattr__(self, "decomposition_contract", contract)
        intents = tuple(self.intents)
        if not intents or len(intents) > 3:
            raise ValueError("a case must contain one to three intents")
        if len({intent.id for intent in intents}) != len(intents):
            raise ValueError("intent ids must be unique within a case")
        object.__setattr__(self, "intents", intents)
