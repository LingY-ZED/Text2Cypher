"""完整方法调用链评测 Oracle 的受限渲染与快照展开测试。"""

from __future__ import annotations

import pytest

from evaluation.call_chain_oracles import (
    CALL_CHAIN_COLUMNS,
    expand_call_chain_snapshot,
    render_call_chain_oracle,
)


def test_method_oracle_renders_all_template_branches_without_placeholder() -> None:
    cypher = render_call_chain_oracle(
        {
            "id": "full_method_call_chain",
            "anchor": {
                "kind": "method",
                "qualified_name": "sample.Service.run",
            },
        }
    )

    assert cypher.count("anchorMethod.全限定名 = 'sample.Service.run'") == 6
    assert cypher.count("UNION") == 5
    assert "<TARGET_METHOD_FILTER>" not in cypher
    assert ";" not in cypher


def test_entry_api_oracle_requires_all_disambiguation_fields() -> None:
    cypher = render_call_chain_oracle(
        {
            "id": "full_method_call_chain",
            "anchor": {
                "kind": "entry_api",
                "http_method": "POST",
                "api_path": "/api/example",
                "service_name": "sample-service",
            },
        }
    )

    assert cypher.count("EXISTS {") == 6
    assert "api.HTTP方法 = 'POST'" in cypher
    assert "api.API路径 = '/api/example'" in cypher
    assert "service.服务名称 = 'sample-service'" in cypher
    assert "图谱版本 = 'v1'" not in cypher


@pytest.mark.parametrize(
    "specification",
    (
        {},
        {"id": "full_method_call_chain", "anchor": {"kind": "method"}},
        {"id": "unknown", "anchor": {"kind": "method", "qualified_name": "x"}},
        {
            "id": "full_method_call_chain",
            "anchor": {"kind": "raw_cypher", "predicate": "RETURN 1"},
        },
    ),
)
def test_oracle_rejects_incomplete_or_unrestricted_specs(
    specification: object,
) -> None:
    with pytest.raises(ValueError):
        render_call_chain_oracle(specification)


def test_snapshot_expansion_preserves_all_table_columns_and_nulls() -> None:
    rows = expand_call_chain_snapshot(
        {
            "defaults": {
                **{column: None for column in CALL_CHAIN_COLUMNS},
                "根方法": "sample.Service.run",
                "图谱版本": "v1",
            },
            "rows": [
                {
                    "层级": 0,
                    "链路类型": "local",
                    "方法路径": ["sample.Service.run"],
                }
            ],
        },
        CALL_CHAIN_COLUMNS,
    )

    assert rows == (
        {
            **{column: None for column in CALL_CHAIN_COLUMNS},
            "根方法": "sample.Service.run",
            "图谱版本": "v1",
            "层级": 0,
            "链路类型": "local",
            "方法路径": ["sample.Service.run"],
        },
    )


def test_snapshot_rejects_unknown_and_missing_columns() -> None:
    with pytest.raises(ValueError, match="未知列"):
        expand_call_chain_snapshot(
            {"defaults": {"未知": None}, "rows": [{}]},
            CALL_CHAIN_COLUMNS,
        )
    with pytest.raises(ValueError, match="缺少"):
        expand_call_chain_snapshot(
            {"defaults": {}, "rows": [{}]},
            CALL_CHAIN_COLUMNS,
        )
