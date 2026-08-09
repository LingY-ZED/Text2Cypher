from __future__ import annotations

import pytest

from text2cypher.components.prompt_builder import DefaultPromptBuilder
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    DependencyParameter,
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
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
    schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="Endpoint",
                properties=(
                    PropertySchema("方法名"),
                    PropertySchema("API类型"),
                    PropertySchema("目标微服务"),
                    PropertySchema("服务名称"),
                ),
            ),
        ),
        relationships=(
            RelationshipSchema(
                name="REL",
                properties=(
                    PropertySchema("调用类型"),
                    PropertySchema("消息流类型"),
                    PropertySchema("交换机名称"),
                    PropertySchema("队列名称"),
                ),
            ),
        ),
    )

    prompt = DefaultPromptBuilder().build(schema, "分析依赖")

    assert "可适用的业务语义：" in prompt.user
    assert "`上游API`" in prompt.user
    assert "`下游API`" in prompt.user
    assert "`目标微服务` 表示业务调用目标" in prompt.user
    assert "`远程调用` 表示代码方法直接访问下游 API" in prompt.user
    assert "方法到下游 API 的关系不得使用 `跨服务调用`" in prompt.user
    assert "`跨服务调用`" in prompt.user
    assert "业务目标服务应优先使用源 API 的 `目标微服务` 属性" in prompt.user
    assert "DISTINCT 关系计数" in prompt.user
    assert "`服务间消息依赖`" in prompt.user
    assert "两端节点类型都提供 `服务名称`" in prompt.user
    assert "关系属性 `交换机名称`" not in prompt.user
    assert "关系属性 `队列名称`" not in prompt.user
    assert "可适用的业务语义" not in prompt.system

    unrelated_prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema(name="City"),)),
        "列出城市",
    )
    assert "可适用的业务语义：" not in unrelated_prompt.user
    assert "上游API" not in unrelated_prompt.user
    assert "跨服务调用" not in unrelated_prompt.user


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


def test_partial_class_method_identifier_semantics_require_all_properties() -> None:
    method_schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="CodeUnit",
                properties=(
                    PropertySchema("方法名"),
                    PropertySchema("所属类名"),
                    PropertySchema("全限定名"),
                ),
            ),
        ),
    )
    incomplete_schema = GraphSchema(
        nodes=(
            NodeSchema(
                name="CodeUnit",
                properties=(PropertySchema("方法名"),),
            ),
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
    assert "不得把该短标识直接作为 `全限定名`" in method_prompt.user
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
    assert prompt.user.endswith("只输出 Cypher：")


def test_explicit_empty_examples_preserve_exact_zero_shot_prompt() -> None:
    schema = GraphSchema(nodes=(NodeSchema("City"),))

    zero_shot = DefaultPromptBuilder().build(schema, "列出城市")
    fallback = DefaultPromptBuilder().build(schema, "列出城市", ())

    assert fallback == zero_shot
    assert "参考示例" not in fallback.user
    assert "参考示例" not in fallback.system


def test_prompt_renders_dependency_contract_without_parameter_values() -> None:
    prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema("Entity"),)),
        "使用上游实体查询归属",
        dependency_parameters=(
            DependencyParameter(
                "dep_q1_rows",
                "q1",
                ("实体标识", "实体类型"),
            ),
        ),
        required_output_columns=("下游实体标识",),
    )

    assert "可用依赖参数：" in prompt.user
    assert "$dep_q1_rows: LIST<MAP>，来自 q1" in prompt.user
    assert "`实体标识`、`实体类型`" in prompt.user
    assert "父结果输出契约：" in prompt.user
    assert "`下游实体标识`" in prompt.user
    assert prompt.user.index("可用依赖参数：") < prompt.user.index("用户问题：")
    assert "不得猜测、拼接或硬编码参数实际值" in prompt.system
    assert "UNWIND $参数名 AS row" in prompt.system
    assert "$参数名[].键名" in prompt.system
    assert "UNWIND $参数名 AS row" in prompt.user
    assert "UNWIND $dep_q1_rows AS row_q1" in prompt.user
    assert "row_q1.`实体标识`" in prompt.user
    assert "每个列必须逐行返回 JSON 标量" in prompt.user
    assert "禁止使用 collect()、列表推导、Map、节点或关系" in prompt.user
    assert "不得使用 collect()、列表推导、Map、节点或关系" in prompt.system


def test_prompt_keeps_original_question_as_semantic_context_for_subtask() -> None:
    prompt = DefaultPromptBuilder().build(
        GraphSchema(nodes=(NodeSchema("Entity"),)),
        "统计这些实体的属性",
        original_question="先找出符合条件的实体，再统计这些实体的属性",
    )

    assert prompt.user.index("原始用户问题：") < prompt.user.index("当前子任务：")
    assert prompt.user.index("当前子任务：") < prompt.user.index("只输出 Cypher：")
    assert "先找出符合条件的实体，再统计这些实体的属性" in prompt.user
    assert "统计这些实体的属性" in prompt.user
    assert "原始用户问题决定实体、限定条件、返回语义和业务含义" in prompt.system


def test_prompt_rejects_blank_explicit_original_question() -> None:
    with pytest.raises(PromptBuildError, match="原始问题不能为空"):
        DefaultPromptBuilder().build(
            GraphSchema(),
            "当前子任务",
            original_question="   ",
        )


def test_prompt_rejects_invalid_required_output_columns() -> None:
    with pytest.raises(PromptBuildError, match="不能重复"):
        DefaultPromptBuilder().build(
            GraphSchema(),
            "问题",
            required_output_columns=("标识", "标识"),
        )
