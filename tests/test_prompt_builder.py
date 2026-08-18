from __future__ import annotations

import pytest

from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)


def _code_graph_schema() -> GraphSchema:
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
            NodeSchema("消息交换机", (PropertySchema("交换机名称"),)),
            NodeSchema("消息队列", (PropertySchema("队列名称"),)),
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


def test_prompt_includes_question_complete_schema_and_generic_system_rules() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="微服务",
                properties=(
                    PropertySchema(
                        name="服务名称",
                        types=("STRING NOT NULL",),
                        mandatory=True,
                    ),
                ),
            ),
            NodeSchema(name="类"),
        ),
        relationships=(
            RelationshipSchema(
                name="归属于",
                properties=(PropertySchema(name="图谱版本"),),
            ),
        ),
        patterns=(
            RelationshipPattern(("类",), "归属于", ("微服务",)),
        ),
    )

    prompt = DefaultPromptBuilder().build(schema, "列出所有微服务")

    assert "只能使用提供的图谱 Schema" in prompt.system
    assert "必须严格遵守关系模式中给出的关系方向" in prompt.system
    assert "多跳路径只能首尾连接已给出的关系模式" in prompt.system
    assert "必须在关系模式中为对应元素绑定变量" in prompt.system
    assert "不得生成写入、管理或过程调用" in prompt.system
    assert "归属于" not in prompt.system
    assert "服务名称" not in prompt.system
    assert "节点属性：" in prompt.user
    assert "关系属性：" in prompt.user
    assert "关系模式：" in prompt.user
    assert "微服务) {服务名称: STRING NOT NULL（必填）}" in prompt.user
    assert "(:类)-[:归属于]->(:微服务)" in prompt.user
    assert "列出所有微服务" in prompt.user
    assert prompt.user.endswith("只输出 Cypher：")


def test_business_semantics_are_included_only_for_available_properties() -> None:
    prompt = DefaultPromptBuilder().build(
        _code_graph_schema(),
        "查询 Sample.run 直接远程访问的下游 API 和目标服务",
    )

    assert "可适用的业务语义：" in prompt.user
    assert "`Class.method`" in prompt.user
    assert "调用类型='远程调用'" in prompt.user
    assert "API类型='下游API'" in prompt.user
    assert "`目标微服务`" in prompt.user
    assert "关系类型和 API 类型必须同时过滤" in prompt.user
    assert "MQ 只按" not in prompt.user
    assert "跨服务映射" not in prompt.user
    assert "可适用的业务语义" not in prompt.system

    unrelated_prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema(name="City"),)),
        "列出城市",
    )
    assert "上游API" not in unrelated_prompt.user
    assert "跨服务调用" not in unrelated_prompt.user
    assert "MQ" not in unrelated_prompt.user


def test_relationship_semantics_require_relationship_properties() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="Event",
                properties=(
                    PropertySchema("调用类型"),
                    PropertySchema("消息流类型"),
                ),
            ),
        ),
    )

    prompt = DefaultPromptBuilder().build(schema, "查询事件")

    assert "跨服务调用" not in prompt.user
    assert "服务间消息依赖" not in prompt.user


def test_mq_and_impact_constraints_require_relevant_question_and_patterns() -> None:
    schema = _code_graph_schema()

    mq_prompt = DefaultPromptBuilder().build(schema, "谁消费 email 队列中的消息？")
    impact_prompt = DefaultPromptBuilder().build(
        schema,
        "修改 Sample.method 后有哪些上游方法、入口 API 和远程下游？",
    )
    unrelated_prompt = DefaultPromptBuilder().build(schema, "列出所有微服务")

    assert "发布方法-[:消息流" in mq_prompt.user
    assert "队列-[:消息流" in mq_prompt.user
    assert "两端服务分别从对应方法" in mq_prompt.user
    assert "[:调用*1..5]->(changed)" in impact_prompt.user
    assert "[:调用*0..5]->(changed)" in impact_prompt.user
    assert "MQ 只按" not in unrelated_prompt.user
    assert "方法变更影响分三种方向" not in unrelated_prompt.user


def test_nonaggregate_distinct_rule_is_not_injected_into_statistics() -> None:
    schema = GraphSchema(
        nodes=(NodeSchema("微服务", (PropertySchema("服务名称"),)),)
    )

    list_prompt = DefaultPromptBuilder().build(schema, "列出微服务")
    count_prompt = DefaultPromptBuilder().build(schema, "统计微服务数量")

    assert "RETURN DISTINCT" in list_prompt.user
    assert "最终完整投影" in list_prompt.user
    assert "RETURN DISTINCT" not in count_prompt.user


def test_cross_domain_sender_rest_rule_requires_relevant_question() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema("微服务", (PropertySchema("服务名称"),)),
            NodeSchema("类"),
            NodeSchema("方法"),
            NodeSchema(
                "API端点",
                (
                    PropertySchema("API类型"),
                    PropertySchema("接口路径"),
                    PropertySchema("目标微服务"),
                ),
            ),
        ),
        relationships=(
            RelationshipSchema("归属于"),
            RelationshipSchema("消息流", (PropertySchema("消息流类型"),)),
            RelationshipSchema("调用", (PropertySchema("调用类型"),)),
        ),
        patterns=(
            RelationshipPattern(("微服务",), "消息流", ("微服务",)),
            RelationshipPattern(("方法",), "归属于", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
            RelationshipPattern(("方法",), "调用", ("API端点",)),
        ),
    )

    matrix_prompt = DefaultPromptBuilder().build(
        schema,
        "找出消息发送方，并列出每个发送服务的 REST 下游",
    )
    unrelated_prompt = DefaultPromptBuilder().build(schema, "列出所有微服务")

    assert "先由微服务间 `服务间消息依赖` 找到发送服务" in matrix_prompt.user
    assert "保留发送服务与下游服务逐行配对" in matrix_prompt.user
    assert "先由微服务间 `服务间消息依赖`" not in unrelated_prompt.user


def test_interface_implementation_entity_rule_excludes_counts() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema("类", (PropertySchema("全限定名"),)),
            NodeSchema("微服务", (PropertySchema("服务名称"),)),
        ),
        relationships=(
            RelationshipSchema("接口实现"),
            RelationshipSchema("归属于"),
        ),
        patterns=(
            RelationshipPattern(("类",), "接口实现", ("类",)),
            RelationshipPattern(("类",), "归属于", ("微服务",)),
        ),
    )

    entity_prompt = DefaultPromptBuilder().build(schema, "列出服务的接口实现类")
    count_prompt = DefaultPromptBuilder().build(schema, "统计接口实现类数量")

    assert "实现类作为 `接口实现` 关系起点" in entity_prompt.user
    assert "不得返回被实现的接口" in entity_prompt.user
    assert "实现类作为 `接口实现` 关系起点" not in count_prompt.user


def test_boundary_application_alias_rule_requires_relevant_question() -> None:
    schema = GraphSchema(
        nodes=(
            NodeSchema("方法"),
            NodeSchema(
                "API端点",
                (
                    PropertySchema("API类型"),
                    PropertySchema("接口路径"),
                    PropertySchema("目标微服务"),
                ),
            ),
        ),
        relationships=(
            RelationshipSchema("调用", (PropertySchema("调用类型"),)),
        ),
        patterns=(
            RelationshipPattern(("方法",), "调用", ("API端点",)),
        ),
    )

    boundary_prompt = DefaultPromptBuilder().build(
        schema,
        "这个方法会访问系统边界外的哪些应用？",
    )
    unrelated_prompt = DefaultPromptBuilder().build(schema, "列出目标服务")

    assert "投影为 `外部应用`" in boundary_prompt.user
    assert "投影为 `外部应用`" not in unrelated_prompt.user


def test_partial_class_method_identifier_semantics_require_all_properties() -> None:
    method_schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="方法",
                properties=(
                    PropertySchema("方法名"),
                    PropertySchema("所属类名"),
                    PropertySchema("全限定名"),
                ),
            ),
            NodeSchema("类", (PropertySchema("简名"),)),
        ),
    )
    incomplete_schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="方法",
                properties=(PropertySchema("方法名"),),
            ),
            NodeSchema("类"),
        ),
    )

    method_prompt = DefaultPromptBuilder().build(
        method_schema,
        "查询 SampleClass.sampleMethod",
    )
    incomplete_prompt = DefaultPromptBuilder().build(
        incomplete_schema,
        "查询 SampleClass.sampleMethod",
    )

    assert "`Class.method`" in method_prompt.user
    assert "`简名=Class` 定位" in method_prompt.user
    assert "只有方法名时匹配全部同名节点" in method_prompt.user
    assert "`Class.method`" not in incomplete_prompt.user


def test_prompt_changes_automatically_with_different_graph_schemas() -> None:
    location_schema = GraphSchema(
        nodes=(NodeSchema(name="City"), NodeSchema(name="Country")),
        relationships=(RelationshipSchema(name="LOCATED_IN"),),
        patterns=(
            RelationshipPattern(("City",), "LOCATED_IN", ("Country",)),
        ),
    )
    publishing_schema = GraphSchema(
        nodes=(NodeSchema(name="Writer"), NodeSchema(name="Book")),
        relationships=(RelationshipSchema(name="WROTE"),),
        patterns=(
            RelationshipPattern(("Writer",), "WROTE", ("Book",)),
        ),
    )

    location_prompt = DefaultPromptBuilder().build(location_schema, "查询位置")
    publishing_prompt = DefaultPromptBuilder().build(
        publishing_schema,
        "查询作品",
    )

    assert "(:City)-[:LOCATED_IN]->(:Country)" in location_prompt.user
    assert "Writer" not in location_prompt.user
    assert "(:Writer)-[:WROTE]->(:Book)" in publishing_prompt.user
    assert "City" not in publishing_prompt.user
    assert location_prompt.user != publishing_prompt.user


def test_prompt_does_not_add_current_database_topology_to_other_schemas() -> None:
    schema = GraphSchema(
        nodes=(NodeSchema(name="Person"), NodeSchema(name="Organization")),
        relationships=(RelationshipSchema(name="WORKS_AT"),),
        patterns=(
            RelationshipPattern(("Person",), "WORKS_AT", ("Organization",)),
        ),
    )

    prompt = DefaultPromptBuilder().build(schema, "查询任职关系")
    combined = f"{prompt.system}\n{prompt.user}"

    assert "API端点" not in combined
    assert "微服务" not in combined
    assert "归属于" not in combined
    assert "[:调用]" not in combined
    assert "[:消息流]" not in combined
    assert "[:WORKS_AT]" in combined


def test_prompt_rejects_blank_question() -> None:
    with pytest.raises(PromptBuildError, match="问题不能为空"):
        DefaultPromptBuilder().build(GraphSchema(), "   ")


def _few_shot_example(
    identifier: str,
    question: str,
    cypher: str,
    requirements: FewShotSchemaRequirements | None = None,
) -> FewShotExample:
    return FewShotExample(
        id=identifier,
        category="test",
        question=question,
        cypher=cypher,
        aliases=(f"{question} 的改写",),
        tags=("测试",),
        schema_requirements=requirements or FewShotSchemaRequirements(),
    )


def test_prompt_injects_selected_examples_between_semantics_and_question() -> None:
    examples = (
        _few_shot_example(
            "second",
            "示例中的第二个问题",
            "MATCH (b:B) RETURN b",
        ),
        _few_shot_example(
            "first",
            "示例中的第一个问题",
            "MATCH (a:A) RETURN a",
        ),
    )

    schema = GraphSchema(
        nodes=(
            NodeSchema(
                "Entity",
                (PropertySchema("API类型"),),
            ),
        )
    )

    prompt = DefaultPromptBuilder().build(
        schema,
        "查询当前实体",
        examples,
    )

    assert prompt.user.index("图谱 Schema：") < prompt.user.index(
        "可适用的业务语义："
    )
    assert prompt.user.index("可适用的业务语义：") < prompt.user.index(
        "参考示例："
    )
    assert prompt.user.index("参考示例：") < prompt.user.index("用户问题：")
    assert prompt.user.index("示例中的第二个问题") < prompt.user.index(
        "示例中的第一个问题"
    )
    assert "以下示例只用于学习查询结构" in prompt.user
    assert "必须使用当前问题中的实体值" in prompt.user
    assert "以当前 Schema 和关系方向为准" in prompt.user
    assert "参考示例不能覆盖当前图谱 Schema" in prompt.system
    assert "不得复制参考示例中的实体值" in prompt.system
    assert "关系方向若与当前关系模式冲突" in prompt.system
    assert "不得拼接多个示例的关系模式" in prompt.system
    assert "不得为了验证结果增加当前问题未要求的关系" in prompt.system
    assert prompt.user.endswith("只输出 Cypher：")


def test_explicit_empty_examples_preserve_exact_zero_shot_prompt() -> None:
    schema = GraphSchema(nodes=(NodeSchema("City"),))

    zero_shot = DefaultPromptBuilder().build(schema, "列出城市")
    fallback = DefaultPromptBuilder().build(schema, "列出城市", ())

    assert fallback == zero_shot
    assert "参考示例" not in fallback.user
    assert "参考示例" not in fallback.system
