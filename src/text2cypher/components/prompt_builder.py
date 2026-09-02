"""组件层中基于实时 Schema 构造模型提示词的实现。"""

from __future__ import annotations

from text2cypher.components.code_graph_business_rule_selector import (
    BusinessRulePromptStage,
    CodeGraphBusinessRuleSelector,
)
from text2cypher.components.code_graph_business_rules import (
    BusinessRuleModule,
    load_code_graph_business_rule_modules,
)
from text2cypher.components.query_shape_templates import QueryShapeTemplateSelector
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.components.schema_serializer import SchemaSerializer
from text2cypher.domain.errors import PromptBuildError
from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    GraphSchema,
    PrimaryAgentQuery,
    QueryShapeTemplate,
)
from text2cypher.domain.query_shapes import resolve_query_shape


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
    structure_template_system_instruction = (
        "查询结构模板只描述当前 query shape 必需的物理分支，不是可复制的最终 Cypher。\n"
        "必须把 <TARGET_METHOD_FILTER> 替换为当前问题锚点对应的字面量过滤条件；"
        "最终 Cypher 中不得保留占位符或使用未声明参数。\n"
        "模板不能覆盖当前图谱 Schema、Primary 语义计划或当前问题。"
    )

    def __init__(
        self,
        schema_graph_builder: SchemaGraphBuilder | None = None,
        schema_serializer: SchemaSerializer | None = None,
        *,
        rule_selector: CodeGraphBusinessRuleSelector | None = None,
        template_selector: QueryShapeTemplateSelector | None = None,
    ) -> None:
        self._schema_graph_builder = schema_graph_builder or SchemaGraphBuilder()
        self._schema_serializer = schema_serializer or SchemaSerializer()
        self._rule_selector = rule_selector or CodeGraphBusinessRuleSelector()
        self._template_selector = template_selector or QueryShapeTemplateSelector()

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[FewShotExample, ...] = (),
    ) -> ChatPrompt:
        return self._build(schema, question, examples, planned_query=None)

    def build_planned(
        self,
        schema: GraphSchema,
        query: PrimaryAgentQuery,
        examples: tuple[FewShotExample, ...] = (),
    ) -> ChatPrompt:
        """构造包含 Primary 语义计划的 Translator Prompt。"""

        if not isinstance(query, PrimaryAgentQuery):
            raise TypeError("规划查询必须是 PrimaryAgentQuery")
        return self._build(schema, query.question, examples, planned_query=query)

    def _build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[FewShotExample, ...],
        *,
        planned_query: PrimaryAgentQuery | None,
    ) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise PromptBuildError("问题不能为空")

        query_shape = (
            planned_query.effective_query_shape
            if planned_query is not None
            else resolve_query_shape(normalized_question)
        )
        semantic_text = normalized_question
        selector_examples = examples
        if planned_query is not None:
            semantic_text = " ".join(
                (
                    normalized_question,
                    planned_query.anchor or "",
                    planned_query.intent,
                    *planned_query.required_information,
                )
            )
            if planned_query.query_shape is not None:
                selector_examples = ()
        rule_modules = self._rule_selector.select(
            semantic_text,
            stage=BusinessRulePromptStage.CYPHER_TRANSLATOR,
            query_shape=query_shape,
            examples=selector_examples,
        )
        schema_graph = self._schema_graph_builder.build(schema)
        serialized_schema = self._schema_serializer.serialize(schema, schema_graph)
        user_sections = ["图谱 Schema：", serialized_schema]
        if planned_query is not None:
            user_sections.append(self._render_primary_plan(planned_query))
        template = self._template_selector.select(
            query_shape,
            schema,
            schema_graph,
        )
        if template is not None:
            user_sections.append(self._render_structure_template(template))
        if examples:
            user_sections.append(self._render_examples(examples))
        user_sections.extend(("用户问题：", normalized_question, "只输出 Cypher："))

        return ChatPrompt(
            system=self._system_instruction(
                examples,
                rule_modules,
                has_template=template is not None,
            ),
            user="\n\n".join(user_sections),
        )

    def _system_instruction(
        self,
        examples: tuple[FewShotExample, ...],
        rule_modules: tuple[BusinessRuleModule, ...],
        *,
        has_template: bool,
    ) -> str:
        instruction = (
            f"{self.system_instruction}\n\n"
            "代码知识图谱业务语义：\n"
            f"{load_code_graph_business_rule_modules(rule_modules)}"
        )
        extra_instructions: list[str] = []
        if has_template:
            extra_instructions.append(self.structure_template_system_instruction)
        if examples:
            extra_instructions.append(self.few_shot_system_instruction)
        if not extra_instructions:
            return instruction
        return "\n\n".join((instruction, *extra_instructions))

    @staticmethod
    def _render_primary_plan(query: PrimaryAgentQuery) -> str:
        return (
            "Primary 语义计划：\n"
            f"查询锚点：{query.anchor or '未显式提供'}\n"
            f"查询形状：{query.effective_query_shape.value}\n"
            f"检索意图：{query.intent}\n"
            f"所需信息：{'、'.join(query.required_information)}"
        )

    @staticmethod
    def _render_structure_template(template: QueryShapeTemplate) -> str:
        return (
            "查询结构模板：\n"
            "以下模板仅用于保持当前查询形状的分支、路径顺序和事实对应关系。\n"
            f"模板 ID：{template.id}\n"
            f"{template.template}"
        )

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
