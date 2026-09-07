"""Primary Agent 所需的纯 Prompt 构造与响应解析。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from text2cypher.domain.models import (
    ChatPrompt,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
)
from text2cypher.domain.query_shapes import QueryShape, resolve_query_shape
from text2cypher.skills.registry import (
    load_primary_agent_semantic_capabilities,
)

_JSON_FENCE = re.compile(
    r"\A```json[ \t]*\r?\n(?P<document>[\s\S]*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE,
)
_LEGACY_QUERY_FIELDS = {"question", "intent", "required_information"}
_PLANNED_QUERY_FIELDS = _LEGACY_QUERY_FIELDS | {"anchor", "query_shape"}
_DEPENDENT_QUERY_CUES = (
    "上述",
    "这些方法",
    "这些对象",
    "其中",
    "前述",
    "该结果",
    "每个结果",
    "上一步",
    "前一查询",
)
_ATOMIC_PATH_SHAPES = {
    QueryShape.ORDERED_METHOD_PATH,
    QueryShape.FULL_ENTRY_CHAIN,
    QueryShape.FULL_DOWNSTREAM_CHAIN,
}
_SERVICE_ANCHOR = re.compile(r"^ts-[a-z0-9-]+-service$", re.IGNORECASE)
_SERVICE_NAME = re.compile(r"\b(ts-[a-z0-9-]+-service)\b", re.IGNORECASE)
_API_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(/[A-Za-z0-9_./{}:-]+)")
_HTTP_METHOD = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
    re.IGNORECASE,
)
_GRAPH_VERSION = re.compile(r"图谱版本\s*(?:为|=|：|:)?\s*(v\d+)\b", re.IGNORECASE)
_METHOD_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
)
_SIMPLE_METHOD_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*的?"
    r"(?:完整|端到端)?调用链"
)
_FULL_METHOD_SPLIT_MARKERS = ("服务内", "rest", "mq")
_FULL_METHOD_RESULT_FIELDS = (
    (
        "根方法",
        "图谱版本",
        "源服务",
        "源API路径",
        "源HTTP方法",
        "方法路径",
        "叶子方法",
    ),
    (
        "根方法",
        "图谱版本",
        "REST层级",
        "源服务",
        "源API路径",
        "源HTTP方法",
        "源方法",
        "方法路径",
        "下游API路径",
        "目标服务",
        "目标API路径",
        "目标HTTP方法",
        "目标方法",
    ),
    (
        "根方法",
        "图谱版本",
        "源服务",
        "源API路径",
        "源HTTP方法",
        "发布方法",
        "发布方法路径",
        "消息交换机",
        "消息队列",
        "路由键",
        "目标服务",
        "消费方法",
        "消费者方法路径",
    ),
)


class PrimaryAgentResponseError(ValueError):
    """Primary Agent 的模型响应格式不符合严格 JSON 契约。"""


class PrimaryAgentPlanError(ValueError):
    """Primary Agent 响应可解析但无法形成有效计划。"""


class PrimaryAgentPromptBuilder:
    """构造与物理图 Schema 解耦的 Primary Agent Prompt。"""

    system_instruction = (
        "你是 Primary LLM Agent，负责把用户问题规划为一到多个可独立执行的"
        "自然语言检索任务。\n"
        "只分析问题需要从代码知识图谱取得什么信息；不要生成 Cypher、数据库答案、"
        "存储实现细节、标签、关系、属性或数据库值。\n"
        "分析摘要只能是简洁的可审计任务说明，不要输出隐藏推理过程。\n"
        "简单问题必须保留为原问题的唯一查询。只有多个意图能够分别独立重算、无需"
        "消费其他查询结果时才拆分。每个查询必须自包含，重复固定对象、方向、条件、"
        "范围和返回语义；不得使用‘这些对象’、‘上述服务’、‘每个结果’等未绑定指代。\n"
        "判断是否拆分时以原问题要求的独立结果集合为单位，不得因为这些集合共享同一"
        "锚点或写在同一句话中就合并；输出前必须检查是否遗漏了可独立计算的并列集合。\n"
        "不得按返回列机械拆分：同一固定对象的对应字段和直接关系、同一 API 的请求与"
        "响应信息、同一有序调用路径、同一消息路径或同一分组的维度与聚合值都必须保留"
        "在一个查询中。完整方法调用链是固定例外，必须按服务内、REST、MQ 三种独立视图"
        "拆分，三条查询都重复原始锚点和限定条件。拆分后的每个查询仍须完整表达调用方向；"
        "严格区分‘哪些方法调用 X’"
        "和‘X 调用哪些方法或服务’。不得自行添加原问题没有的直接、间接、可达性、"
        "完整路径或额外关联范围。聚合同时涉及对象和度量时，明确要求‘按每个对象给出"
        "度量’，不可简化为总数。每个查询还必须给出固定查询锚点和唯一 query_shape；"
        "query_shape 只描述检索结构，不描述物理 Schema。只返回 JSON，不返回 Markdown、"
        "解释或其他文本。"
    )

    def build(self, question: str, max_queries: int) -> ChatPrompt:
        normalized_question = self._normalize_question(question)
        self._validate_max_queries(max_queries)
        user = "\n\n".join(
            (
                "用户问题：\n" + normalized_question,
                f"复杂问题最多生成 {max_queries} 个查询。",
                "只返回 JSON：\n"
                '{"analysis_summary":"简洁任务摘要","queries":['
                '{"question":"完整子问题","anchor":"固定对象",'
                '"query_shape":"查询形状","intent":"检索意图",'
                '"required_information":["需要的信息"]}]}',
                "query_shape 只能是：\n"
                + ", ".join(shape.value for shape in QueryShape),
            )
        )
        if (
            resolve_query_shape(normalized_question)
            is QueryShape.FULL_METHOD_CALL_CHAIN
        ):
            user = "\n\n".join(
                (
                    user,
                    "本题固定规划要求：\n"
                    "必须正好输出三个 query_shape=general 的查询，顺序固定为："
                    "服务内调用明细、REST 跨服务明细、MQ 发布消费明细。每条查询都必须"
                    "逐字包含原问题中的 API 路径或方法锚点，并重复 HTTP 方法、服务名、"
                    "图谱版本等显式限定；不得使用上述、这些、上一步或前一查询等指代。\n"
                    "三个 required_information 分别使用以下完整字段：\n"
                    "1. 根方法、图谱版本、源服务、源API路径、源HTTP方法、"
                    "方法路径、叶子方法；\n"
                    "2. 根方法、图谱版本、REST层级、源服务、源API路径、"
                    "源HTTP方法、源方法、方法路径、下游API路径、目标服务、"
                    "目标API路径、目标HTTP方法、目标方法；\n"
                    "3. 根方法、图谱版本、源服务、源API路径、源HTTP方法、"
                    "发布方法、发布方法路径、"
                    "消息交换机、消息队列、路由键、目标服务、消费方法、消费者方法路径。\n"
                    "子问题正文使用‘服务内调用明细’、‘REST 跨服务明细’、"
                    "‘MQ 发布消费明细’，不要在子问题正文中使用‘调用链’、"
                    "‘完整下游调用链’、"
                    "‘有序方法路径’或其他会改变 query_shape 的措辞。\n"
                    "完整 JSON 规划示例（必须保留本题实体与限定）：\n"
                    + _render_full_method_plan_example(normalized_question),
                )
            )
        return ChatPrompt(
            system=(
                f"{self.system_instruction}\n\n"
                "可检索业务能力：\n"
                f"{load_primary_agent_semantic_capabilities()}"
            ),
            user=user,
        )

    @staticmethod
    def _normalize_question(question: str) -> str:
        if type(question) is not str:
            raise TypeError("问题必须是字符串")
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空")
        return normalized_question

    @staticmethod
    def _validate_max_queries(max_queries: int) -> None:
        if type(max_queries) is not int or not 2 <= max_queries <= 3:
            raise ValueError("max_queries 必须在 2 到 3 之间")


class PrimaryAgentResponseParser:
    """严格解析 Primary Agent 返回的结构化检索计划。"""

    def parse(
        self,
        content: str,
        original_question: str,
        max_queries: int,
    ) -> PrimaryAgentPlan:
        normalized_original = PrimaryAgentPromptBuilder._normalize_question(
            original_question
        )
        PrimaryAgentPromptBuilder._validate_max_queries(max_queries)
        payload = self._parse_document(content)
        if set(payload) != {"analysis_summary", "queries"}:
            raise PrimaryAgentResponseError("规划响应字段不符合契约")

        analysis_summary = self._parse_text(
            payload["analysis_summary"],
            "analysis_summary",
        )
        raw_queries = payload["queries"]
        if not isinstance(raw_queries, list):
            raise PrimaryAgentResponseError("queries 必须是列表")
        if not 1 <= len(raw_queries) <= max_queries:
            raise PrimaryAgentPlanError("规划查询数量超出允许范围")

        queries = tuple(
            self._parse_query(raw_query, index)
            for index, raw_query in enumerate(raw_queries, start=1)
        )
        if len(queries) == 1 and queries[0].question != normalized_original:
            queries = (replace(queries[0], question=normalized_original),)
        try:
            plan = PrimaryAgentPlan(
                original_question=normalized_original,
                analysis_summary=analysis_summary,
                queries=queries,
            )
        except (TypeError, ValueError) as error:
            raise PrimaryAgentPlanError("规划内容不符合领域约束") from error
        self._validate_semantics(plan)
        return plan

    def _parse_query(self, raw_query: object, index: int) -> PrimaryAgentQuery:
        if not isinstance(raw_query, dict):
            raise PrimaryAgentResponseError("查询项必须是对象")
        fields = set(raw_query)
        if fields not in (_LEGACY_QUERY_FIELDS, _PLANNED_QUERY_FIELDS):
            raise PrimaryAgentResponseError("查询项字段不符合契约")
        raw_required_information = raw_query["required_information"]
        if not isinstance(raw_required_information, list):
            raise PrimaryAgentResponseError("required_information 必须是列表")
        required_information = tuple(
            self._parse_text(item, "required_information")
            for item in raw_required_information
        )
        try:
            anchor: str | None = None
            query_shape: QueryShape | None = None
            if fields == _PLANNED_QUERY_FIELDS:
                anchor = self._parse_text(raw_query["anchor"], "anchor")
                raw_shape = self._parse_text(
                    raw_query["query_shape"],
                    "query_shape",
                )
                try:
                    query_shape = QueryShape(raw_shape)
                except ValueError:
                    raise PrimaryAgentResponseError(
                        "query_shape 不在允许范围内"
                    ) from None
            return PrimaryAgentQuery(
                query_id=f"q{index}",
                question=self._parse_text(raw_query["question"], "question"),
                intent=self._parse_text(raw_query["intent"], "intent"),
                required_information=required_information,
                anchor=anchor,
                query_shape=query_shape,
            )
        except PrimaryAgentResponseError:
            raise
        except (TypeError, ValueError) as error:
            raise PrimaryAgentPlanError("查询项不符合领域约束") from error

    @staticmethod
    def _validate_semantics(plan: PrimaryAgentPlan) -> None:
        original_shape = resolve_query_shape(plan.original_question)
        if original_shape is QueryShape.FULL_METHOD_CALL_CHAIN:
            PrimaryAgentResponseParser._validate_full_method_split(plan)
        if plan.decomposed:
            if original_shape in _ATOMIC_PATH_SHAPES:
                raise PrimaryAgentPlanError("有序或完整调用路径不得拆分")
            if any(
                cue in query.question
                for query in plan.queries
                for cue in _DEPENDENT_QUERY_CUES
            ):
                raise PrimaryAgentPlanError("子查询依赖其他查询结果，无法独立执行")

        for query in plan.queries:
            if query.query_shape is None:
                continue
            if (
                query.query_shape is not QueryShape.GENERAL
                and query.anchor is not None
                and _SERVICE_ANCHOR.fullmatch(query.anchor)
            ):
                raise PrimaryAgentPlanError(
                    "方法调用 QueryShape 不能用于服务级 REST 对应查询"
                )
            inferred_shape = resolve_query_shape(query.question)
            if (
                inferred_shape is not QueryShape.GENERAL
                and inferred_shape is not query.query_shape
            ):
                raise PrimaryAgentPlanError("query_shape 与子问题明确措辞冲突")

    @staticmethod
    def _validate_full_method_split(plan: PrimaryAgentPlan) -> None:
        if len(plan.queries) != 3:
            raise PrimaryAgentPlanError("完整方法调用链必须拆成三个查询")
        if any(query.query_shape is not QueryShape.GENERAL for query in plan.queries):
            raise PrimaryAgentPlanError("完整方法调用链子查询必须使用 general")
        anchors = tuple(query.anchor for query in plan.queries)
        if anchors[0] is None or len(set(anchors)) != 1:
            raise PrimaryAgentPlanError("完整方法调用链子查询必须重复同一锚点")
        anchor = anchors[0]
        assert anchor is not None
        constraints = _explicit_call_chain_constraints(plan.original_question)
        for index, (query, marker, fields) in enumerate(
            zip(
                plan.queries,
                _FULL_METHOD_SPLIT_MARKERS,
                _FULL_METHOD_RESULT_FIELDS,
                strict=True,
            ),
            start=1,
        ):
            question = query.question.lower()
            if marker not in question:
                raise PrimaryAgentPlanError("完整方法调用链子查询顺序不合法")
            if anchor.lower() not in question:
                raise PrimaryAgentPlanError("完整方法调用链子查询必须自包含锚点")
            if any(value.lower() not in question for value in constraints):
                raise PrimaryAgentPlanError("完整方法调用链子查询遗漏显式限定")
            if not set(fields) <= set(query.required_information):
                raise PrimaryAgentPlanError(
                    f"完整方法调用链第 {index} 个子查询返回字段不完整"
                )

    @staticmethod
    def _parse_text(value: object, field_name: str) -> str:
        if type(value) is not str or not value.strip():
            raise PrimaryAgentResponseError(f"{field_name} 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _parse_document(content: str) -> dict[str, Any]:
        if type(content) is not str or not content.strip():
            raise PrimaryAgentResponseError("规划响应不能为空")
        document = content.strip()
        fence_match = _JSON_FENCE.fullmatch(document)
        if fence_match is not None:
            document = fence_match.group("document")
        elif document.startswith("```"):
            raise PrimaryAgentResponseError("规划响应不是标准 JSON fence")

        try:
            payload = json.loads(document)
        except json.JSONDecodeError as error:
            raise PrimaryAgentResponseError("规划响应不是 JSON") from error
        if not isinstance(payload, dict):
            raise PrimaryAgentResponseError("规划 JSON 根节点必须是对象")
        return payload


def _explicit_call_chain_constraints(question: str) -> tuple[str, ...]:
    """Return explicit qualifiers that every independent branch must repeat."""

    values: list[str] = []
    for pattern in (_API_PATH, _HTTP_METHOD, _SERVICE_NAME, _GRAPH_VERSION):
        match = pattern.search(question)
        if match is not None:
            values.append(match.group(1) if match.lastindex else match.group(0))
    for marker in ("未映射", "映射缺失"):
        if marker in question:
            values.append(marker)
    return tuple(dict.fromkeys(values))


def _render_full_method_plan_example(question: str) -> str:
    anchor = _call_chain_prompt_anchor(question)
    constraints = _explicit_call_chain_constraints(question)
    descriptor = "、".join(constraints) or anchor
    questions = (
        f"以 {descriptor} 为固定锚点，查询服务内调用明细，最多十跳",
        f"以 {descriptor} 为固定锚点，查询 REST 跨服务明细，最多两层",
        f"以 {descriptor} 为固定锚点，查询 MQ 发布消费明细，最多一层",
    )
    intents = (
        "查询根服务内按路径签名和位置索引连续的方法序列",
        "查询 REST 出口、目标服务、目标 API 和目标入口方法",
        "查询 MQ 发布、路由、消费及消费者后续方法序列",
    )
    return json.dumps(
        {
            "analysis_summary": "分别查询服务内、REST 和 MQ 三类调用明细",
            "queries": [
                {
                    "question": child_question,
                    "anchor": anchor,
                    "query_shape": QueryShape.GENERAL.value,
                    "intent": intent,
                    "required_information": list(fields),
                }
                for child_question, intent, fields in zip(
                    questions,
                    intents,
                    _FULL_METHOD_RESULT_FIELDS,
                    strict=True,
                )
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _call_chain_prompt_anchor(question: str) -> str:
    for pattern in (_API_PATH, _METHOD_REFERENCE, _SIMPLE_METHOD_REFERENCE):
        match = pattern.search(question)
        if match is not None:
            return match.group(1)
    return "原问题指定的方法或入口"
