from __future__ import annotations

import pytest

from text2cypher.components.code_graph_semantics import (
    CodeGraphSemanticSelector,
)
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)


def _schema() -> GraphSchema:
    return GraphSchema(
        nodes=(
            NodeSchema(
                "方法",
                (PropertySchema("方法名"), PropertySchema("全限定名")),
            ),
            NodeSchema("类", (PropertySchema("简名"),)),
            NodeSchema("微服务", (PropertySchema("服务名称"),)),
            NodeSchema(
                "API端点",
                (
                    PropertySchema("API类型"),
                    PropertySchema("接口路径"),
                    PropertySchema("目标微服务"),
                ),
            ),
            NodeSchema("消息交换机"),
            NodeSchema("消息队列"),
        ),
        relationships=(
            RelationshipSchema("归属于"),
            RelationshipSchema("调用", (PropertySchema("调用类型"),)),
            RelationshipSchema("消息流", (PropertySchema("消息流类型"),)),
        ),
        patterns=(
            RelationshipPattern(("方法",), "调用", ("方法",)),
            RelationshipPattern(("方法",), "调用", ("API端点",)),
            RelationshipPattern(("API端点",), "调用", ("API端点",)),
            RelationshipPattern(("API端点",), "归属于", ("方法",)),
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
            RelationshipPattern(("方法",), "消息流", ("消息交换机",)),
            RelationshipPattern(("消息交换机",), "消息流", ("消息队列",)),
            RelationshipPattern(("消息队列",), "消息流", ("方法",)),
            RelationshipPattern(("微服务",), "消息流", ("微服务",)),
        ),
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("查询 Sample.run 的直接上游方法", {"direct", "upstream"}),
        (
            "查询 getAllFood 的完整上游调用链",
            {"chain", "upstream", "entry_api", "rest", "cross_service"},
        ),
        (
            "查询 getAllFood 的完整下游调用路径",
            {"chain", "downstream", "rest", "cross_service"},
        ),
        ("查询可到达 Sample.run 的入口 API", {"upstream", "entry_api"}),
        ("查询方法远程访问的下游 API", {"downstream", "rest"}),
        ("查询跨服务 REST 调用", {"rest", "cross_service"}),
        ("查询完整 MQ 消息链路", {"chain", "mq"}),
        ("按服务统计 API 数量", {"aggregate"}),
    ],
)
def test_selector_detects_business_features(
    question: str,
    expected: set[str],
) -> None:
    actual = {feature.value for feature in CodeGraphSemanticSelector().detect(question)}

    assert expected <= actual


def test_full_upstream_chain_rules_keep_anchor_entry_and_order() -> None:
    rules = CodeGraphSemanticSelector().select(
        _schema(),
        "查询 getAllFood 的完整上游调用链",
    )
    rendered = "\n".join(rules)

    assert "只有方法名时匹配全部同名节点" in rendered
    assert "上游反向、下游正向" in rendered
    assert "入口方法沿 `[:调用*0..5]`" in rendered
    assert "有序方法全限定名列表" in rendered
    assert "`path` 必须在目标方法结束" in rendered
    assert len(rules) <= 5
    assert len(rendered) <= 1000


def test_full_downstream_chain_uses_optional_direct_service_boundary() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "查询 getAllFood 的完整下游调用链",
        )
    )

    assert "沿 `[:调用*0..5]` 正向展开" in rendered
    assert "`调用类型='远程调用'`" in rendered
    assert "`API类型='下游API'`" in rendered
    assert "必须返回目标方法" in rendered
    assert "映射缺失时仍保留" in rendered
    assert "服务边界只展开一层" in rendered
    assert "入口 API：" not in rendered


def test_explicit_direct_method_does_not_expand_to_complete_chain() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "查询 FoodServiceImpl.getAllFood 的直接上游方法",
        )
    )

    assert "‘直接/下一跳’只匹配一跳" in rendered
    assert "完整上游链" not in rendered
    assert "入口 API：" not in rendered


def test_unrelated_list_does_not_receive_call_rest_or_mq_semantics() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(_schema(), "列出全部微服务")
    )

    assert "[:调用" not in rendered
    assert "REST" not in rendered
    assert "MQ" not in rendered


def test_service_upstream_chain_uses_service_anchor_and_correlated_rest_shape() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "查询到达 ts-order-other-service 的 REST 上游跨服务链，"
            "保持调用服务、调用方法、下游 API 和目标上游 API 完整对应。",
        )
    )

    assert "目标为服务的 REST 上游链" in rendered
    assert "目标只绑定在下游 API.`目标微服务`" in rendered
    assert "绝不能绑定成目标服务" in rendered
    assert "所有字段保持逐行配对" in rendered
    assert "方法锚点" not in rendered
    assert "完整下游链：" not in rendered


def test_service_outgoing_api_pair_uses_ownership_and_optional_boundary() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "列出 ts-admin-basic-info-service 远程调用的下游 API，并保持"
            "调用方法、目标服务、目标上游 API 和入口方法的完整配对。",
        )
    )

    assert "固定服务的 REST 出口配对" in rendered
    assert "微服务←类←方法归属链" in rendered
    assert "下游与目标 API 必须逐行配对" in rendered
    assert "入口 API：" not in rendered


def test_simple_class_anchor_uses_short_name_and_separate_service_resources() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "从 AdminRouteServiceImpl 出发返回所属服务，并列出该服务的公开 API。",
        )
    )

    assert "不含包路径的类名必须用 `类.简名`" in rendered
    assert "不得把短类名作为 `类.全限定名`" in rendered
    assert "资源自身沿完整资源→方法→类→微服务归属路径" in rendered


def test_direct_mq_consumer_does_not_receive_full_message_path() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "汇总 email 消息的消费方法",
        )
    )

    assert "直接查询队列消费者" in rendered
    assert "不得把消费方向写成方法指向队列" in rendered
    assert "MQ 只按发布方法" not in rendered


def test_service_mq_dependency_uses_service_level_relationship() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "汇总通过 MQ 向 ts-notification-service 发送消息的服务",
        )
    )

    assert "服务间消息依赖" in rendered
    assert "不要改用详细消息链" in rendered


def test_rest_target_count_requires_grouping_by_downstream_service() -> None:
    rendered = "\n".join(
        CodeGraphSemanticSelector().select(
            _schema(),
            "对 ts-admin-basic-info-service 给出 REST 目标调用数",
        )
    )

    assert "按下游 API.`目标微服务` 分组" in rendered
    assert "AS 调用关系数" in rendered
