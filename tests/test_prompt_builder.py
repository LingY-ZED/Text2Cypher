from __future__ import annotations

import pytest

from text2cypher.components.code_graph_business_rules import (
    BusinessRuleModule,
    load_code_graph_business_rule_module,
)
from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.components.query_shape_templates import (
    JsonQueryShapeTemplateLoader,
)
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    NodeSchema,
    PrimaryAgentQuery,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.infrastructure.few_shot import JsonFewShotExampleLoader


def test_prompt_includes_question_complete_schema_and_selected_rules() -> None:
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
    core = load_code_graph_business_rule_module(BusinessRuleModule.CORE)
    anchor = load_code_graph_business_rule_module(
        BusinessRuleModule.ANCHOR_OWNERSHIP
    )

    assert "只能使用提供的图谱 Schema" in prompt.system
    assert "不得生成写入、管理或过程调用" in prompt.system
    assert prompt.system.count(core) == 1
    assert prompt.system.count(anchor) == 1
    assert "代码知识图谱业务语义：" in prompt.system
    assert "方法-[:服务于]->上游API" not in prompt.system
    assert "发布至.路由键 = 路由至.路由键" not in prompt.system
    assert "代码知识图谱业务语义：" not in prompt.user
    assert "节点属性：" in prompt.user
    assert "关系属性：" in prompt.user
    assert "关系模式：" in prompt.user
    assert "微服务) {服务名称: STRING NOT NULL（必填）}" in prompt.user
    assert "(:类)-[:归属于]->(:微服务)" in prompt.user
    assert "列出所有微服务" in prompt.user
    assert prompt.user.endswith("只输出 Cypher：")


def test_unknown_scene_injects_only_core_rules() -> None:
    prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema(name="City"),)),
        "用另一种说法描述城市列表",
    )
    core = load_code_graph_business_rule_module(BusinessRuleModule.CORE)

    assert prompt.system.count(core) == 1
    assert "上游入口固定为" not in prompt.system
    assert "详细消息路径固定为" not in prompt.system
    assert "API端点" not in prompt.user
    assert "微服务" not in prompt.user
    assert "可适用的业务语义" not in prompt.user
    assert "当前 Schema 已观察到的结构" in prompt.system


def test_prompt_injects_selected_examples_after_schema() -> None:
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
    prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema("Entity"),)),
        "查询当前实体",
        examples,
    )

    assert prompt.user.index("图谱 Schema：") < prompt.user.index("参考示例：")
    assert prompt.user.index("参考示例：") < prompt.user.index("用户问题：")
    assert prompt.user.index("示例中的第二个问题") < prompt.user.index(
        "示例中的第一个问题"
    )
    assert "以下示例只用于学习查询结构" in prompt.user
    assert "必须使用当前问题中的实体值" in prompt.user
    assert "参考示例不能覆盖当前图谱 Schema" in prompt.system
    assert "不得复制参考示例中的实体值" in prompt.system
    assert "关系方向若与当前关系模式冲突" in prompt.system
    assert "不得拼接多个示例的关系模式" in prompt.system
    assert "不得为了验证结果增加当前问题未要求的关系" in prompt.system
    assert prompt.system.count(
        load_code_graph_business_rule_module(BusinessRuleModule.CORE)
    ) == 1


def test_prompt_changes_with_dynamic_schema_without_changing_rules() -> None:
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
    publishing_prompt = DefaultPromptBuilder().build(publishing_schema, "查询作品")

    assert "(:City)-[:LOCATED_IN]->(:Country)" in location_prompt.user
    assert "Writer" not in location_prompt.user
    assert "(:Writer)-[:WROTE]->(:Book)" in publishing_prompt.user
    assert "City" not in publishing_prompt.user
    assert location_prompt.user != publishing_prompt.user
    assert location_prompt.system == publishing_prompt.system


def test_prompt_rejects_blank_question() -> None:
    with pytest.raises(PromptBuildError, match="问题不能为空"):
        DefaultPromptBuilder().build(GraphSchema(), "   ")


def test_prompt_does_not_include_primary_agent_internal_contract_fields() -> None:
    prompt = DefaultPromptBuilder().build(GraphSchema(), "查询服务")

    assert "query_shape" not in prompt.user
    assert "requested_fields" not in prompt.user
    assert "查询形状：" not in prompt.user
    assert "请求字段：" not in prompt.user


def test_planned_upstream_prompt_uses_primary_shape_without_example_rule_leak() -> None:
    rest_example = FewShotExample(
        id="rest",
        category="path_query",
        question="查询 REST 下游 API 和目标服务",
        cypher="MATCH (n) RETURN n",
        aliases=("远程调用",),
        tags=("REST",),
        schema_requirements=FewShotSchemaRequirements(),
        query_shape=QueryShape.DIRECT_REST_EGRESS,
    )
    query = PrimaryAgentQuery(
        "q1",
        "查询 FoodServiceImpl.getAllFood 的调用关系",
        "查询所有上游可达方法和调用距离",
        ("目标方法", "上游可达方法", "调用距离"),
        "FoodServiceImpl.getAllFood",
        QueryShape.UPSTREAM_REACHABILITY,
    )

    prompt = DefaultPromptBuilder().build_planned(
        GraphSchema(),
        query,
        (rest_example,),
    )

    method_call = load_code_graph_business_rule_module(
        BusinessRuleModule.METHOD_CALL
    )
    rest = load_code_graph_business_rule_module(BusinessRuleModule.REST)
    ordered = load_code_graph_business_rule_module(BusinessRuleModule.ORDERED_PATH)
    assert method_call in prompt.system
    assert rest not in prompt.system
    assert ordered not in prompt.system
    assert "查询结构模板：" not in prompt.user
    assert "Primary 语义计划：" in prompt.user
    assert "查询形状：upstream_reachability" in prompt.user
    assert prompt.user.index("图谱 Schema：") < prompt.user.index(
        "Primary 语义计划："
    )
    assert prompt.user.index("Primary 语义计划：") < prompt.user.index(
        "参考示例："
    )
    assert prompt.user.index("参考示例：") < prompt.user.index("用户问题：")


def test_full_entry_plan_uses_canonical_few_shot_without_duplicate_template() -> None:
    query = PrimaryAgentQuery(
        "q1",
        "查询 ConsignServiceImpl.updateConsignRecord 的完整入口调用链",
        "查询入口 API 到锚点方法的完整有序链",
        ("目标方法", "入口 API", "有序方法路径"),
        "ConsignServiceImpl.updateConsignRecord",
        QueryShape.FULL_ENTRY_CHAIN,
    )

    example = next(
        item
        for item in JsonFewShotExampleLoader().load()
        if item.id == "call-method-full-entry-chain"
    )
    prompt = DefaultPromptBuilder().build_planned(
        _template_schema(),
        query,
        (example,),
    )

    assert "查询结构模板：" not in prompt.user
    assert "full-entry-chain-structure" not in prompt.user
    assert "参考示例：" in prompt.user
    assert example.question in prompt.user
    assert example.cypher in prompt.user
    assert "UNION" not in prompt.user
    assert prompt.user.index("Primary 语义计划：") < prompt.user.index(
        "参考示例："
    )
    assert prompt.user.index("参考示例：") < prompt.user.index("用户问题：")


def test_full_method_call_chain_plan_injects_the_schema_compatible_template() -> None:
    template = next(
        item
        for item in JsonQueryShapeTemplateLoader().load()
        if item.query_shape is QueryShape.FULL_METHOD_CALL_CHAIN
    )
    requirements = template.schema_requirements
    schema = GraphSchema(
        nodes=tuple(
            NodeSchema(
                label,
                tuple(
                    PropertySchema(name)
                    for name in requirements.node_properties[label]
                ),
            )
            for label in requirements.node_labels
        ),
        relationships=tuple(
            RelationshipSchema(
                relationship_type,
                tuple(
                    PropertySchema(name)
                    for name in requirements.relationship_properties[relationship_type]
                ),
            )
            for relationship_type in requirements.relationship_types
        ),
        patterns=requirements.patterns,
    )
    query = PrimaryAgentQuery(
        "q1",
        "查询 InsidePaymentServiceImpl.pay 的完整调用链。",
        "查询服务内、REST 与 MQ 的完整分段调用链",
        ("根方法", "链路类型", "方法路径"),
        "InsidePaymentServiceImpl.pay",
        QueryShape.FULL_METHOD_CALL_CHAIN,
    )

    prompt = DefaultPromptBuilder().build_planned(schema, query)

    assert "查询结构模板：" in prompt.user
    assert template.id in prompt.user
    assert template.template in prompt.user
    assert prompt.user.count("<TARGET_METHOD_FILTER>") == 6
    assert "UNION" in prompt.user
    assert load_code_graph_business_rule_module(BusinessRuleModule.MQ) in prompt.system


def _template_schema() -> GraphSchema:
    return GraphSchema(
        nodes=(
            NodeSchema(
                "方法",
                tuple(
                    PropertySchema(name)
                    for name in ("所属类名", "方法名", "全限定名")
                ),
            ),
            NodeSchema("上游API", (PropertySchema("API路径"),)),
        ),
        relationships=(
            RelationshipSchema("服务于"),
            RelationshipSchema("接口调用"),
            RelationshipSchema(
                "链中下一节点",
                (PropertySchema("路径签名"), PropertySchema("位置索引")),
            ),
        ),
        patterns=(
            RelationshipPattern(("方法",), "服务于", ("上游API",)),
            RelationshipPattern(("方法",), "接口调用", ("方法",)),
            RelationshipPattern(("方法",), "链中下一节点", ("方法",)),
        ),
    )


def _few_shot_example(
    identifier: str,
    question: str,
    cypher: str,
) -> FewShotExample:
    return FewShotExample(
        id=identifier,
        category="test",
        question=question,
        cypher=cypher,
        aliases=(f"{question} 的改写",),
        tags=("测试",),
        schema_requirements=FewShotSchemaRequirements(),
    )
