from __future__ import annotations

import pytest

from text2cypher.components.cypher_parameter_guard import CypherParameterGuard
from text2cypher.domain.errors import CypherValidationError
from text2cypher.domain.models import DependencyParameter


def _specification() -> DependencyParameter:
    return DependencyParameter(
        name="dep_q1_rows",
        source_id="q1",
        columns=("entity_id", "entity_type"),
    )


def test_parameter_guard_allows_exactly_the_declared_parameter() -> None:
    CypherParameterGuard().validate(
        "UNWIND $dep_q1_rows AS input RETURN input.entity_id",
        {"dep_q1_rows": [{"entity_id": "a", "entity_type": "method"}]},
        (_specification(),),
    )


def test_parameter_guard_does_not_treat_a_string_literal_as_parameter_use() -> None:
    with pytest.raises(CypherValidationError, match="未使用"):
        CypherParameterGuard().validate(
            "RETURN '$dep_q1_rows' AS text",
            {"dep_q1_rows": []},
            (_specification(),),
        )


@pytest.mark.parametrize(
    "cypher, message",
    [
        ("RETURN 1", "未使用"),
        ("RETURN $unknown", "未绑定"),
    ],
)
def test_parameter_guard_rejects_missing_or_unknown_parameters(
    cypher: str,
    message: str,
) -> None:
    with pytest.raises(CypherValidationError, match=message):
        CypherParameterGuard().validate(
            cypher,
            {"dep_q1_rows": []},
            (_specification(),),
        )


def test_parameter_guard_rejects_mismatched_parameter_values() -> None:
    with pytest.raises(ValueError, match="不一致"):
        CypherParameterGuard().validate(
            "UNWIND $dep_q1_rows AS input RETURN input",
            {},
            (_specification(),),
        )
