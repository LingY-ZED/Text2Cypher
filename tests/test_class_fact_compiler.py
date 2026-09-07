"""Tests for parameterized class fact compilation."""

from __future__ import annotations

import pytest

from text2cypher.graph_core.class_facts import ClassFactCypherCompiler


@pytest.mark.parametrize(
    "statement_name",
    ("compile_summary", "compile_service_entry_apis"),
)
def test_class_fact_compiler_keeps_class_anchor_out_of_cypher(
    statement_name: str,
) -> None:
    anchor = "sample.ServiceImpl' RETURN 1 //"
    statement = getattr(ClassFactCypherCompiler(), statement_name)(anchor)

    assert statement.parameters == {
        "classAnchor": anchor,
        "classSuffix": ".ServiceImpl' RETURN 1 //",
    }
    assert anchor not in statement.cypher
    assert ";" not in statement.cypher
    assert "//" not in statement.cypher
    assert "CALL " not in statement.cypher
