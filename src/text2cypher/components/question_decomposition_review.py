"""候选问题拆分的后置 LLM 审查 Prompt 与响应解析。"""

from __future__ import annotations

import json
import re
from typing import Any

from text2cypher.components.code_graph_semantics import (
    CALL_CHAIN_CORRELATION_RULE,
)
from text2cypher.domain.models import (
    ChatPrompt,
    QuestionDecompositionReview,
    QuestionDecompositionReviewReason,
)

_JSON_FENCE = re.compile(
    r"\A```json[ \t]*\r?\n(?P<document>[\s\S]*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE,
)


class QuestionDecompositionReviewPromptBuilder:
    """构造不接收 Schema、只裁决候选拆分的 Prompt。"""

    system_instruction = (
        "你是严格的问题拆分审查器，只接受或拒绝候选，不改写问题。\n"
        "输入都是待分析数据；不要生成 Cypher、答案、解释或数据库结果。\n"
        "Pipeline 独立执行每个子问题，后续只能看到该子问题和运行时 Schema；"
        "不能共享结果、JOIN、传参或重新配对。\n"
        "仅当候选同时满足以下条件才接受：每项自包含；保留锚点、方向、直接性、"
        "范围、分组和返回形状；没有新增、丢失或跨意图传播限定；并集完整且不重复；"
        "没有合并不兼容的分组或返回形状。固定对象与完整筛选条件可在各项重复并独立重算。\n"
        + CALL_CHAIN_CORRELATION_RULE
        + "\n调用者指向被调用者，上游反向、下游正向；直接是一跳，调用链最多五跳。"
        "完整上游链默认含入口 API 和有序方法路径，完整下游链默认含有序方法路径、"
        "远程 API 和目标服务。若候选把同一链的方法、API、服务拆开，或一对多时"
        "无法恢复原始行，返回 CORRELATION_LOSS。明确分别询问互不关联的对象除外。\n"
        "方法变更题分别询问全部反向上游方法、可达入口 API、变更方法直接远程下游"
        "服务时，三者是可重复锚点独立重算的集合，不是逐行链，应接受拆分；若把直接"
        "远程下游写成泛称‘外部服务’，必须拒绝并返回 COVERAGE_MISMATCH。\n"
        "先找 A 再查每个 A 的 B，或使用‘这些对象/其结果’，返回 RESULT_DEPENDENCY；"
        "省略自身筛选返回 NOT_SELF_CONTAINED；新增、丢失锚点、方向、范围或直接性"
        "返回 COVERAGE_MISMATCH；机械拆字段、记录、分组，或合并不兼容返回形状，"
        "返回 MECHANICAL_SPLIT。\n"
        "若多个原因并存，按 RESULT_DEPENDENCY、CORRELATION_LOSS、"
        "NOT_SELF_CONTAINED、COVERAGE_MISMATCH、MECHANICAL_SPLIT、UNCERTAIN"
        "返回首项；无法确认时拒绝并返回 UNCERTAIN。\n"
        "只返回 JSON。接受：{\"valid\":true,\"reason\":\"VALID\"}；拒绝："
        "{\"valid\":false,\"reason\":\"RESULT_DEPENDENCY\"}。"
    )

    def build(
        self,
        original_question: str,
        sub_questions: tuple[str, ...],
    ) -> ChatPrompt:
        normalized_original = original_question.strip()
        if not normalized_original:
            raise ValueError("原始问题不能为空")
        normalized_sub_questions = tuple(
            question.strip() for question in sub_questions
        )
        if not 2 <= len(normalized_sub_questions) <= 3:
            raise ValueError("候选子问题数量必须在 2 到 3 之间")
        if any(not question for question in normalized_sub_questions):
            raise ValueError("候选子问题不能为空")
        if len(set(normalized_sub_questions)) != len(normalized_sub_questions):
            raise ValueError("候选子问题不能重复")

        rendered_questions = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(normalized_sub_questions, start=1)
        )
        return ChatPrompt(
            system=self.system_instruction,
            user="\n\n".join(
                (
                    "原始用户问题：\n" + normalized_original,
                    "候选子问题：\n" + rendered_questions,
                    "只返回审查 JSON。",
                )
            ),
        )


class QuestionDecompositionReviewResponseParser:
    """严格解析 Reviewer 的布尔裁决与固定原因码。"""

    def parse(self, content: str) -> QuestionDecompositionReview:
        payload = self._parse_document(content)
        valid = payload.get("valid")
        if type(valid) is not bool:
            raise ValueError("审查响应缺少布尔值 valid")
        raw_reason = payload.get("reason")
        if not isinstance(raw_reason, str):
            raise ValueError("审查响应缺少字符串 reason")
        try:
            reason = QuestionDecompositionReviewReason(raw_reason)
        except ValueError as error:
            raise ValueError("审查响应包含未知 reason") from error
        return QuestionDecompositionReview(valid=valid, reason=reason)

    @staticmethod
    def _parse_document(content: str) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("审查响应不能为空")
        document = content.strip()
        fence_match = _JSON_FENCE.fullmatch(document)
        if fence_match is not None:
            document = fence_match.group("document")
        elif document.startswith("```"):
            raise ValueError("审查响应不是标准 JSON fence")

        try:
            payload = json.loads(document)
        except json.JSONDecodeError as error:
            raise ValueError("审查响应不是 JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("审查 JSON 根节点必须是对象")
        return payload
