"""Loading and validation for the frozen version-6 evaluation dataset."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from evaluation.call_chain_oracles import (
    CALL_CHAIN_VIEW_COLUMNS,
    CallChainView,
    expand_call_chain_snapshot,
    project_call_chain_rows,
    render_call_chain_oracle,
    render_call_chain_view_oracle,
)
from evaluation.models import (
    ComparisonMode,
    DecompositionContract,
    Difficulty,
    EvaluationCase,
    EvaluationIntent,
    ValueNormalizer,
)

DEFAULT_CASES_PATH = Path(__file__).with_name("cases.json")
EXPECTED_DIFFICULTIES = {
    Difficulty.SIMPLE: 10,
    Difficulty.MEDIUM: 16,
    Difficulty.HARD: 18,
}
EXPECTED_CASE_COUNT = sum(EXPECTED_DIFFICULTIES.values())
EXPECTED_VERSION = 6


def load_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[EvaluationCase, ...]:
    """Load the complete dataset and reject incomplete or ambiguous fixtures."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("evaluation dataset cannot be loaded") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("evaluation dataset root must contain a cases list")
    if payload.get("version") != EXPECTED_VERSION:
        raise ValueError(
            f"evaluation dataset version must be {EXPECTED_VERSION}"
        )
    cases = tuple(_parse_case(item) for item in payload["cases"])
    _validate_suite(cases)
    return cases


def _parse_case(value: object) -> EvaluationCase:
    data = _object(value, "case")
    intents = data.get("intents")
    if not isinstance(intents, list):
        raise ValueError("case intents must be a list")
    try:
        difficulty = Difficulty(data["difficulty"])
    except (KeyError, ValueError) as error:
        raise ValueError("case difficulty is invalid") from error
    contract = DecompositionContract(
        data.get("decomposition_contract", DecompositionContract.ANY.value)
    )
    parsed_intents = tuple(_parse_intent(item) for item in intents)
    if (
        contract is DecompositionContract.MUST_SPLIT
        and len(intents) == 1
        and _is_full_method_call_chain_intent(intents[0])
    ):
        parsed_intents = _split_call_chain_intents(intents[0], parsed_intents[0])
    return EvaluationCase(
        id=_required(data, "id"),
        difficulty=difficulty,
        category=_required(data, "category"),
        question=_required(data, "question"),
        intents=parsed_intents,
        decomposition_contract=contract,
    )


def _is_full_method_call_chain_intent(value: object) -> bool:
    data = _object(value, "intent")
    template = data.get("oracle_template")
    return (
        isinstance(template, dict)
        and template.get("id") == "full_method_call_chain"
    )


def _split_call_chain_intents(
    raw_intent: object,
    full_intent: EvaluationIntent,
) -> tuple[EvaluationIntent, ...]:
    data = _object(raw_intent, "intent")
    specification = data["oracle_template"]
    metadata = (
        (CallChainView.LOCAL, "服务内方法调用明细"),
        (CallChainView.REST, "REST 跨服务调用明细"),
        (CallChainView.MQ, "MQ 发布消费明细"),
    )
    return tuple(
        EvaluationIntent(
            id=f"{view.value}_call_chain",
            label=label,
            oracle_cypher=render_call_chain_view_oracle(specification, view),
            comparison_mode=ComparisonMode.ROW_SET,
            expected_columns=CALL_CHAIN_VIEW_COLUMNS[view],
            accepted_aliases={
                column: (column,) for column in CALL_CHAIN_VIEW_COLUMNS[view]
            },
            expected_snapshot=project_call_chain_rows(
                full_intent.expected_snapshot,
                view,
            ),
        )
        for view, label in metadata
    )


def _parse_intent(value: object) -> EvaluationIntent:
    data = _object(value, "intent")
    try:
        mode = ComparisonMode(data["comparison_mode"])
    except (KeyError, ValueError) as error:
        raise ValueError("intent comparison_mode is invalid") from error
    columns = data.get("expected_columns")
    aliases = data.get("accepted_aliases")
    normalizers = data.get("value_normalizers", {})
    snapshot = data.get("expected_snapshot")
    if not isinstance(columns, list) or not all(isinstance(v, str) for v in columns):
        raise ValueError("expected_columns must be a string list")
    if not isinstance(aliases, dict):
        raise ValueError("accepted_aliases must be an object")
    parsed_aliases: dict[str, tuple[str, ...]] = {}
    for column, values in aliases.items():
        if not isinstance(column, str) or not isinstance(values, list):
            raise ValueError("accepted_aliases values must be string lists")
        if not all(isinstance(alias, str) for alias in values):
            raise ValueError("accepted_aliases values must be string lists")
        parsed_aliases[column] = tuple(values)
    if not isinstance(normalizers, dict) or not all(
        isinstance(column, str) and isinstance(normalizer, str)
        for column, normalizer in normalizers.items()
    ):
        raise ValueError("value_normalizers must be a string object")
    try:
        parsed_normalizers = {
            column: ValueNormalizer(normalizer)
            for column, normalizer in normalizers.items()
        }
    except ValueError as error:
        raise ValueError("value_normalizers contains an invalid strategy") from error
    if "oracle_template" in data:
        if "oracle_cypher" in data:
            raise ValueError("oracle_template 与 oracle_cypher 不能同时存在")
        oracle_cypher = render_call_chain_oracle(data["oracle_template"])
    else:
        oracle_cypher = _required(data, "oracle_cypher")
    if "call_chain_snapshot" in data:
        if "expected_snapshot" in data:
            raise ValueError(
                "call_chain_snapshot 与 expected_snapshot 不能同时存在"
            )
        snapshot = list(
            expand_call_chain_snapshot(data["call_chain_snapshot"], columns)
        )
    if not isinstance(snapshot, list) or not all(
        isinstance(row, dict) for row in snapshot
    ):
        raise ValueError("expected_snapshot must be an object list")
    return EvaluationIntent(
        id=_required(data, "id"),
        label=_required(data, "label"),
        oracle_cypher=oracle_cypher,
        comparison_mode=mode,
        expected_columns=tuple(columns),
        accepted_aliases=parsed_aliases,
        expected_snapshot=tuple(dict(row) for row in snapshot),
        value_normalizers=parsed_normalizers,
    )


def _validate_suite(cases: tuple[EvaluationCase, ...]) -> None:
    if len(cases) != EXPECTED_CASE_COUNT:
        raise ValueError(
            "evaluation dataset must contain "
            f"{EXPECTED_CASE_COUNT} cases, got {len(cases)}"
        )
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case ids must be unique")
    if len({case.question for case in cases}) != len(cases):
        raise ValueError("case questions must be unique")
    counts = Counter(case.difficulty for case in cases)
    if counts != Counter(EXPECTED_DIFFICULTIES):
        raise ValueError(f"difficulty distribution is invalid: {dict(counts)}")


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"missing required field: {key}")
    return data[key]
