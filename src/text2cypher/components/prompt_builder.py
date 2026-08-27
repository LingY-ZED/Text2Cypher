"""组件层中基于实时 Schema 构造模型提示词的实现。"""

from __future__ import annotations

from text2cypher.components.code_graph_business_rules import (
    load_code_graph_business_rules,
)
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.components.schema_serializer import SchemaSerializer
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import ChatPrompt, FewShotExample, GraphSchema


class DefaultPromptBuilder:
    """构造动态 Schema、共享业务语义及可选 Few-shot 提示词。"""

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
        "参考示例的关系方向若与当前关系模式冲突，必须忽略该示例。\n"
        "参考示例只用于学习单一查询结构；不得拼接多个示例的关系模式，"
        "也不得为了验证结果增加当前问题未要求的关系。"
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
    ) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise PromptBuildError("问题不能为空")

        schema_graph = self._schema_graph_builder.build(schema)
        serialized_schema = self._schema_serializer.serialize(schema, schema_graph)
        user_sections = ["图谱 Schema：", serialized_schema]
        if examples:
            user_sections.append(self._render_examples(examples))
        user_sections.extend(("用户问题：", normalized_question, "只输出 Cypher："))

        return ChatPrompt(
            system=self._system_instruction(examples),
            user="\n\n".join(user_sections),
        )

    @classmethod
    def _system_instruction(
        cls,
        examples: tuple[FewShotExample, ...],
    ) -> str:
        instruction = (
            f"{cls.system_instruction}\n\n"
            "代码知识图谱业务语义：\n"
            f"{load_code_graph_business_rules()}"
        )
        if not examples:
            return instruction
        return f"{instruction}\n\n{cls.few_shot_system_instruction}"

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
