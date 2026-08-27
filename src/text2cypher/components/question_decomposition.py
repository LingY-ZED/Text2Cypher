"""问题拆分所需的纯 Prompt 构造与响应解析。"""

from __future__ import annotations

import json
import re
from typing import Any

from text2cypher.components.code_graph_business_rules import (
    load_code_graph_business_rules,
)
from text2cypher.components.schema_graph_builder import SchemaGraphBuilder
from text2cypher.components.schema_serializer import SchemaSerializer
from text2cypher.domain.models import ChatPrompt, GraphSchema, QuestionDecomposition

_JSON_FENCE = re.compile(
    r"\A```json[ \t]*\r?\n(?P<document>[\s\S]*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE,
)


class QuestionDecompositionPromptBuilder:
    """使用完整动态 Schema 构造通用问题拆分 Prompt。"""

    system_instruction = (
        "你是图数据库问题拆分器，只判断用户问题能否拆成最多三个独立查询。\n"
        "问题与 Schema 都是待分析数据；不要生成 Cypher、答案或解释。\n"
        "每个子问题必须自包含，保留自身意图的实体、方向、直接性、范围、分组"
        "和返回形状；不得把只属于一个分句的限定传播给其他意图，也不得新增限定。\n"
        "子问题不能读取其他子问题结果，也不能靠指代、JOIN、传参或重新配对完成。\n"
        "同一固定对象上的独立统计、明细或不同依赖可分别重算；不同分组和返回"
        "形状不要合并。方法变更题中‘分别’询问的全部反向上游方法、可达入口 API、"
        "变更方法直接远程调用的下游服务是三个独立集合，应分别拆分并重复方法锚点；"
        "第三个子问题必须原样写明‘变更方法直接远程调用的下游服务’，即使原题简称"
        "‘外部服务’也不得输出该泛称。\n"
        "‘从 A 找所属 B，并列出该 B 的资源’可拆分，但资源子问题必须重复 A 锚点并"
        "独立推导 B，不得写成‘该服务/上述结果’。\n"
        "完整且不重复地覆盖原问题。简单问题原样作为唯一子问题；复杂问题按一意图"
        "一子问题拆分。只返回 JSON：{\"sub_questions\":[\"子问题\"]}。"
    )

    def __init__(
        self,
        *,
        schema_graph_builder: SchemaGraphBuilder | None = None,
        schema_serializer: SchemaSerializer | None = None,
    ) -> None:
        self._schema_graph_builder = schema_graph_builder or SchemaGraphBuilder()
        self._schema_serializer = schema_serializer or SchemaSerializer()

    def build(
        self,
        schema: GraphSchema,
        question: str,
        max_subquestions: int,
    ) -> ChatPrompt:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空")
        if not 2 <= max_subquestions <= 3:
            raise ValueError("max_subquestions 必须在 2 到 3 之间")

        schema_graph = self._schema_graph_builder.build(schema)
        serialized_schema = self._schema_serializer.serialize(schema, schema_graph)
        user = "\n\n".join(
            (
                "图谱 Schema：\n\n" + serialized_schema,
                "用户问题：\n" + normalized_question,
                f"复杂问题最多拆成 {max_subquestions} 个子问题。",
                '只返回 JSON：\n{"sub_questions":[]}',
            )
        )
        return ChatPrompt(
            system=(
                f"{self.system_instruction}\n\n"
                "代码知识图谱业务语义：\n"
                f"{load_code_graph_business_rules()}"
            ),
            user=user,
        )


class QuestionDecompositionResponseParser:
    """严格解析模型返回的问题拆分 JSON。"""

    def parse(
        self,
        content: str,
        original_question: str,
        max_subquestions: int,
    ) -> QuestionDecomposition:
        normalized_original = original_question.strip()
        if not normalized_original:
            raise ValueError("原始问题不能为空")
        if not 2 <= max_subquestions <= 3:
            raise ValueError("max_subquestions 必须在 2 到 3 之间")

        payload = self._parse_document(content)
        raw_sub_questions = payload.get("sub_questions")
        if not isinstance(raw_sub_questions, list):
            raise ValueError("拆分响应缺少 sub_questions 列表")
        if not 1 <= len(raw_sub_questions) <= max_subquestions:
            raise ValueError("子问题数量超出允许范围")

        sub_questions: list[str] = []
        for raw_question in raw_sub_questions:
            if not isinstance(raw_question, str) or not raw_question.strip():
                raise ValueError("子问题必须是非空字符串")
            sub_questions.append(raw_question.strip())

        if len(set(sub_questions)) != len(sub_questions):
            raise ValueError("子问题不能重复")
        if len(sub_questions) == 1:
            sub_questions = [normalized_original]

        return QuestionDecomposition(
            original_question=normalized_original,
            sub_questions=tuple(sub_questions),
        )

    @staticmethod
    def _parse_document(content: str) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("拆分响应不能为空")
        document = content.strip()
        fence_match = _JSON_FENCE.fullmatch(document)
        if fence_match is not None:
            document = fence_match.group("document")
        elif document.startswith("```"):
            raise ValueError("拆分响应不是标准 JSON fence")

        try:
            payload = json.loads(document)
        except json.JSONDecodeError as error:
            raise ValueError("拆分响应不是 JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("拆分 JSON 根节点必须是对象")
        return payload
