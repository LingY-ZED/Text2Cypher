"""完整方法调用链评测 Oracle 的受限渲染与快照展开测试。"""

from __future__ import annotations

import pytest

from evaluation.call_chain_oracles import (
    CALL_CHAIN_COLUMNS,
    LOCAL_CALL_CHAIN_COLUMNS,
    MQ_CALL_CHAIN_COLUMNS,
    REST_CALL_CHAIN_COLUMNS,
    CallChainView,
    expand_call_chain_snapshot,
    project_call_chain_rows,
    render_call_chain_oracle,
    render_call_chain_view_oracle,
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


def test_split_oracles_are_three_independent_statements_with_stable_columns() -> None:
    specification = {
        "id": "full_method_call_chain",
        "anchor": {"kind": "method", "qualified_name": "sample.Service.run"},
    }

    local = render_call_chain_view_oracle(specification, CallChainView.LOCAL)
    rest = render_call_chain_view_oracle(specification, CallChainView.REST)
    mq = render_call_chain_view_oracle(specification, CallChainView.MQ)

    assert "UNION" not in local
    assert "AS 叶子方法" in local
    assert rest.count("UNION") == 1
    assert "AS REST层级" in rest
    assert "secondDownstreamApi.API路径 AS 下游API路径" in rest
    assert "UNION" not in mq
    assert "publishRelation.路由键 = routeRelation.路由键" in mq
    assert "AS 发布方法路径" in mq
    assert "AS 消费者方法路径" in mq
    assert all(";" not in cypher for cypher in (local, rest, mq))


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


def test_snapshot_projects_local_rest_and_mq_tables() -> None:
    defaults = {
        **{column: None for column in CALL_CHAIN_COLUMNS},
        "根方法": "sample.Service.run",
        "图谱版本": "v1",
    }
    rows = tuple(
        {**defaults, **row}
        for row in (
            {
                "层级": 0,
                "链路类型": "local",
                "源服务": "source",
                "源方法": "sample.Service.run",
                "方法路径": ["sample.Service.run"],
                "目标方法": "sample.Service.run",
            },
            {
                "层级": 1,
                "链路类型": "rest",
                "源服务": "source",
                "源方法": "sample.Service.run",
                "方法路径": ["sample.Service.run"],
                "下游API路径": "/target",
            },
            {
                "层级": 1,
                "链路类型": "mq",
                "源服务": "source",
                "源方法": "sample.Service.run",
                "方法路径": ["sample.Service.run"],
                "目标服务": "consumer",
                "目标方法": "consumer.Listener.receive",
                "消息交换机": "exchange",
                "消息队列": "queue",
                "路由键": "key",
            },
            {
                "层级": 1,
                "链路类型": "local",
                "源服务": "consumer",
                "源方法": "consumer.Listener.receive",
                "方法路径": ["consumer.Listener.receive"],
                "目标方法": "consumer.Listener.receive",
                "消息交换机": "exchange",
                "消息队列": "queue",
                "路由键": "key",
            },
        )
    )

    local = project_call_chain_rows(rows, CallChainView.LOCAL)
    rest = project_call_chain_rows(rows, CallChainView.REST)
    mq = project_call_chain_rows(rows, CallChainView.MQ)

    assert tuple(local[0]) == LOCAL_CALL_CHAIN_COLUMNS
    assert tuple(rest[0]) == REST_CALL_CHAIN_COLUMNS
    assert tuple(mq[0]) == MQ_CALL_CHAIN_COLUMNS
    assert local[0]["叶子方法"] == "sample.Service.run"
    assert rest[0]["REST层级"] == 1
    assert mq[0]["消费者方法路径"] == ["consumer.Listener.receive"]


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
