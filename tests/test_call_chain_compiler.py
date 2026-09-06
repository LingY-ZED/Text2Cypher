"""完整调用链确定性编译器的参数化和预算测试。"""

from __future__ import annotations

import pytest

from text2cypher.domain.models import CallChainQuerySpec
from text2cypher.graph_core.call_chain import CallChainCypherCompiler


def test_compiler_keeps_anchor_and_version_out_of_cypher_text() -> None:
    spec = CallChainQuerySpec(
        anchor_qualified_name="sample.Service.run' RETURN 1 //",
        graph_version="version' //",
    )

    statement = CallChainCypherCompiler().compile(spec)

    assert statement.parameters == {
        "anchorQualifiedName": "sample.Service.run' RETURN 1 //",
        "graphVersion": "version' //",
    }
    assert "sample.Service.run" not in statement.cypher
    assert "version'" not in statement.cypher
    assert statement.cypher.count("$anchorQualifiedName") == 6
    assert statement.cypher.count("$graphVersion") == 6
    assert statement.cypher.count("UNION") == 5
    assert "<TARGET_METHOD_FILTER>" not in statement.cypher


def test_compiler_produces_identical_statements_for_the_same_spec() -> None:
    compiler = CallChainCypherCompiler()
    specification = CallChainQuerySpec(
        anchor_qualified_name="sample.Service.run",
        graph_version="v2",
    )

    assert compiler.compile(specification) == compiler.compile(specification)


def test_compiler_reduces_physical_branches_to_the_requested_budget() -> None:
    statement = CallChainCypherCompiler().compile(
        CallChainQuerySpec(
            anchor_qualified_name="sample.Service.run",
            local_hops=2,
            rest_hops=1,
            mq_hops=0,
        )
    )

    assert statement.cypher.count("UNION") == 2
    assert "*0..10" not in statement.cypher
    assert statement.cypher.count("*0..2") == 4
    assert "发布至" not in statement.cypher
    assert "graphVersion = $graphVersion" not in statement.cypher
    assert statement.parameters == {"anchorQualifiedName": "sample.Service.run"}


@pytest.mark.parametrize(
    "kwargs",
    (
        {"local_hops": 11},
        {"local_hops": True},
        {"rest_hops": 3},
        {"mq_hops": 2},
    ),
)
def test_spec_rejects_budget_outside_the_fixed_contract(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        CallChainQuerySpec("sample.Service.run", **kwargs)  # type: ignore[arg-type]
