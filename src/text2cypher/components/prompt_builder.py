"""组件层中基于实时 Schema 构造模型提示词的实现。"""

from __future__ import annotations

from dataclasses import dataclass

from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.components.schema_serializer import SchemaSerializer
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    ChatPrompt,
    DependencyParameter,
    FewShotExample,
    GraphSchema,
)


@dataclass(frozen=True, slots=True)
class _ModelingConstraint:
    text: str
    required_node_properties: frozenset[str] = frozenset()
    required_relationship_properties: frozenset[str] = frozenset()


class DefaultPromptBuilder:
    """构造动态 Schema 约束及可选 Few-shot 示例提示词。"""

    system_instruction = (
        "你是 Neo4j Cypher 专家。\n"
        "只能使用提供的图谱 Schema。\n"
        "必须严格遵守关系模式中给出的关系方向。\n"
        "多跳路径只能首尾连接已给出的关系模式，且每一跳都不得反转。\n"
        "不得使用 Schema 中不存在的节点标签、关系类型或属性。\n"
        "引用节点或关系属性前，必须在关系模式中为对应元素绑定变量。\n"
        "不得生成写入、管理或过程调用。\n"
        "只能返回一条只读 Cypher，不输出解释文字。"
    )
    few_shot_system_instruction = (
        "参考示例不能覆盖当前图谱 Schema。\n"
        "不得复制参考示例中的实体值，必须使用当前问题中的实体值。\n"
        "参考示例的关系方向若与当前关系模式冲突，必须忽略该示例。"
    )
    dependency_system_instruction = (
        "依赖参数规格是受控查询输入，必须使用给出的参数名，"
        "不得猜测、拼接或硬编码参数实际值。\n"
        "依赖参数与当前图谱 Schema 一样优先于参考示例。"
    )
    modeling_constraints = (
        _ModelingConstraint(
            text=(
                "当用户用 `Class.method` 形式提供不含包路径的方法标识时，"
                "应分别使用 `方法名 = method` 和 `所属类名 ENDS WITH '.Class'` "
                "定位；不得把该短标识直接作为 `全限定名` 的精确值。"
            ),
            required_node_properties=frozenset(
                {"方法名", "所属类名", "全限定名"}
            ),
        ),
        _ModelingConstraint(
            text=(
                "属性 `API类型` 的值 `上游API` 表示对外提供的 API，"
                "值 `下游API` 表示访问下游目标的 API。"
            ),
            required_node_properties=frozenset({"API类型"}),
        ),
        _ModelingConstraint(
            text=(
                "属性 `目标微服务` 表示业务调用目标；本地层级或归属位置"
                "不能替代该目标语义。"
            ),
            required_node_properties=frozenset({"目标微服务"}),
        ),
        _ModelingConstraint(
            text=(
                "关系属性 `调用类型` 的值 `远程调用` 表示代码方法直接访问"
                "下游 API；值 `跨服务调用` 只适用于两个 API 端点之间的调用。"
                "方法到下游 API 的关系不得使用 `跨服务调用`。"
            ),
            required_node_properties=frozenset(
                {"方法名", "API类型", "目标微服务"}
            ),
            required_relationship_properties=frozenset({"调用类型"}),
        ),
        _ModelingConstraint(
            text=(
                "REST 跨服务调用由关系属性 `调用类型` 的值 `跨服务调用` 标识；"
                "调用方服务应从源 API 的真实归属路径获取，业务目标服务应优先"
                "使用源 API 的 `目标微服务` 属性，不得用目标 API 的本地归属位置"
                "替代；服务间统计应排除调用方与目标服务相同的结果，并使用"
                "DISTINCT 关系计数避免归属路径扇出导致重复计数。"
            ),
            required_node_properties=frozenset(
                {"API类型", "目标微服务", "服务名称"}
            ),
            required_relationship_properties=frozenset({"调用类型"}),
        ),
        _ModelingConstraint(
            text=(
                "服务间 MQ 依赖由关系属性 `消息流类型` 的值 "
                "`服务间消息依赖` 标识；应选择两端节点类型都提供 `服务名称` "
                "属性的关系模式，并绑定关系变量后读取其属性。"
            ),
            required_node_properties=frozenset({"服务名称"}),
            required_relationship_properties=frozenset({"消息流类型"}),
        ),
        _ModelingConstraint(
            text=(
                "返回服务名称时只能使用 Schema 中实际提供的 `服务名称` 属性，"
                "不得翻译或杜撰属性名。"
            ),
            required_node_properties=frozenset({"服务名称"}),
        ),
    )

    def __init__(
        self,
        schema_graph_builder: SchemaGraphBuilder | None = None,
        schema_serializer: SchemaSerializer | None = None,
    ) -> None:
        self._schema_graph_builder = schema_graph_builder or SchemaGraphBuilder()
        self._schema_serializer = schema_serializer or SchemaSerializer()

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[FewShotExample, ...] = (),
        *,
        dependency_parameters: tuple[DependencyParameter, ...] = (),
        required_output_columns: tuple[str, ...] = (),
    ) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise PromptBuildError("问题不能为空")

        schema_graph = self._schema_graph_builder.build(schema)
        serialized_schema = self._schema_serializer.serialize(schema, schema_graph)
        applicable_constraints = self._render_applicable_constraints(schema)
        user_sections = [
            "图谱 Schema：",
            serialized_schema,
        ]
        if applicable_constraints:
            user_sections.extend(
                [
                    "可适用的业务语义：",
                    applicable_constraints,
                ]
            )
        if examples:
            user_sections.append(self._render_examples(examples))
        if dependency_parameters:
            user_sections.append(
                self._render_dependency_parameters(dependency_parameters)
            )
        if required_output_columns:
            user_sections.append(
                self._render_required_output_columns(required_output_columns)
            )
        user_sections.extend(
            [
                "用户问题：",
                normalized_question,
                "只输出 Cypher：",
            ]
        )

        return ChatPrompt(
            system=self._system_instruction(examples, dependency_parameters),
            user="\n\n".join(user_sections),
        )

    @classmethod
    def _system_instruction(
        cls,
        examples: tuple[FewShotExample, ...],
        dependency_parameters: tuple[DependencyParameter, ...],
    ) -> str:
        instructions = [cls.system_instruction]
        if examples:
            instructions.append(cls.few_shot_system_instruction)
        if dependency_parameters:
            instructions.append(cls.dependency_system_instruction)
        return "\n".join(instructions)

    @staticmethod
    def _render_examples(examples: tuple[FewShotExample, ...]) -> str:
        blocks = [
            "参考示例：",
            "以下示例只用于学习查询结构。\n"
            "必须使用当前问题中的实体值；\n"
            "若示例与当前 Schema 冲突，以当前 Schema 和关系方向为准。",
        ]
        blocks.extend(
            f"示例 {index}：\n问题：{example.question}\n"
            f"Cypher：\n{example.cypher}"
            for index, example in enumerate(examples, start=1)
        )
        return "\n\n".join(blocks)

    @staticmethod
    def _render_dependency_parameters(
        dependency_parameters: tuple[DependencyParameter, ...],
    ) -> str:
        blocks = [
            "可用依赖参数：",
            "每个参数都是 LIST<MAP>；只能使用列出的参数名和键，"
            "不得在 Cypher 中写入或猜测实际参数值。",
        ]
        blocks.extend(
            "- "
            f"${parameter.name}: LIST<MAP>，来自 {parameter.source_id}，"
            "键："
            + "、".join(f"`{column}`" for column in parameter.columns)
            for parameter in dependency_parameters
        )
        return "\n".join(blocks)

    @staticmethod
    def _render_required_output_columns(
        required_output_columns: tuple[str, ...],
    ) -> str:
        normalized = tuple(column.strip() for column in required_output_columns)
        if not normalized or any(not column for column in normalized):
            raise PromptBuildError("必需输出列不能为空")
        if len(set(normalized)) != len(normalized):
            raise PromptBuildError("必需输出列不能重复")
        return (
            "父结果输出契约：\n"
            "必须在 RETURN 中使用 AS 返回以下列名，以供后续子问题参数化使用：\n- "
            + "\n- ".join(f"`{column}`" for column in normalized)
        )

    @classmethod
    def _render_applicable_constraints(cls, schema: GraphSchema) -> str:
        available_node_properties = {
            property_schema.name
            for node in schema.nodes
            for property_schema in node.properties
        }
        available_relationship_properties = {
            property_schema.name
            for relationship in schema.relationships
            for property_schema in relationship.properties
        }
        return "\n".join(
            f"- {constraint.text}"
            for constraint in cls.modeling_constraints
            if (
                constraint.required_node_properties
                <= available_node_properties
                and constraint.required_relationship_properties
                <= available_relationship_properties
            )
        )
