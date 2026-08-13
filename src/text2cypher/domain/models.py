"""领域层的不可变模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}不能为空")
    return normalized


class CypherFailureKind(StrEnum):
    """可由一次性 Corrector 修正的 Cypher 阶段失败类型。"""

    PARSE = "parse"
    VALIDATION = "validation"
    EXECUTION = "execution"
    EMPTY_RESULT = "empty_result"


class CypherFailureSource(StrEnum):
    """纠错失败信息的来源。"""

    LOCAL = "local"
    NEO4J = "neo4j"
    RESULT = "result"


class QuestionDecompositionReviewReason(StrEnum):
    """问题拆分候选的固定审查结论原因。"""

    VALID = "VALID"
    RESULT_DEPENDENCY = "RESULT_DEPENDENCY"
    CORRELATION_LOSS = "CORRELATION_LOSS"
    NOT_SELF_CONTAINED = "NOT_SELF_CONTAINED"
    COVERAGE_MISMATCH = "COVERAGE_MISMATCH"
    MECHANICAL_SPLIT = "MECHANICAL_SPLIT"
    UNCERTAIN = "UNCERTAIN"


class ResultSummaryMode(StrEnum):
    """查询结果自然语言总结的生成方式。"""

    LLM = "llm"
    TEMPLATE = "template"


class ResultSummaryFallbackReason(StrEnum):
    """确定性模板总结的固定降级原因。"""

    DISABLED = "disabled"
    EMPTY_RESULT = "empty_result"
    INPUT_TOO_LARGE = "input_too_large"
    LLM_FAILURE = "llm_failure"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class ResultSummary:
    """一次查询结果的自然语言答案及其生成来源。"""

    answer: str
    mode: ResultSummaryMode
    fallback_reason: ResultSummaryFallbackReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "answer", _require_text(self.answer, "自然语言答案"))
        if not isinstance(self.mode, ResultSummaryMode):
            raise TypeError("总结生成方式必须是 ResultSummaryMode")
        if self.fallback_reason is not None and not isinstance(
            self.fallback_reason,
            ResultSummaryFallbackReason,
        ):
            raise TypeError("总结降级原因必须是 ResultSummaryFallbackReason 或 None")
        if self.mode is ResultSummaryMode.LLM and self.fallback_reason is not None:
            raise ValueError("LLM 总结不能包含降级原因")
        if self.mode is ResultSummaryMode.TEMPLATE and self.fallback_reason is None:
            raise ValueError("模板总结必须包含降级原因")


@dataclass(frozen=True, slots=True)
class QuestionDecompositionReview:
    """一次只接受或拒绝候选拆分的不可变审查结果。"""

    valid: bool
    reason: QuestionDecompositionReviewReason

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise TypeError("审查结果 valid 必须是布尔值")
        if not isinstance(self.reason, QuestionDecompositionReviewReason):
            raise TypeError("审查原因必须是 QuestionDecompositionReviewReason")
        if self.valid != (
            self.reason is QuestionDecompositionReviewReason.VALID
        ):
            raise ValueError("审查结果 valid 与 reason 不一致")


@dataclass(frozen=True, slots=True)
class CypherFailureContext:
    """一次 Cypher 纠错可安全使用的结构化失败上下文。"""

    kind: CypherFailureKind
    source: CypherFailureSource
    message: str
    code: str | None = None
    gql_status: str | None = None
    classification: str | None = None
    line: int | None = None
    column: int | None = None
    offset: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CypherFailureKind):
            raise TypeError("失败类型必须是 CypherFailureKind")
        if not isinstance(self.source, CypherFailureSource):
            raise TypeError("失败来源必须是 CypherFailureSource")
        object.__setattr__(self, "message", _require_text(self.message, "失败信息"))
        for field_name in ("code", "gql_status", "classification"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _require_text(value, field_name),
                )
        for field_name in ("line", "column", "offset"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field_name}必须是非负整数或 None")


@dataclass(frozen=True, slots=True)
class PropertySchema:
    """节点标签或关系类型可用的属性。"""

    name: str
    types: tuple[str, ...] = ()
    mandatory: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "属性名"))
        object.__setattr__(self, "types", tuple(self.types))


@dataclass(frozen=True, slots=True)
class NodeSchema:
    """节点标签及其可用属性。"""

    name: str
    properties: tuple[PropertySchema, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "节点标签"))
        object.__setattr__(self, "properties", tuple(self.properties))


@dataclass(frozen=True, slots=True)
class RelationshipSchema:
    """关系类型及其可用属性。"""

    name: str
    properties: tuple[PropertySchema, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _require_text(self.name, "关系类型")
        )
        object.__setattr__(self, "properties", tuple(self.properties))


@dataclass(frozen=True, slots=True)
class RelationshipPattern:
    """图中观测到的有向关系模式。"""

    start_labels: tuple[str, ...]
    relationship_type: str
    end_labels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_labels", tuple(self.start_labels))
        object.__setattr__(self, "end_labels", tuple(self.end_labels))
        object.__setattr__(
            self,
            "relationship_type",
            _require_text(self.relationship_type, "关系类型"),
        )


@dataclass(frozen=True, slots=True, order=True)
class SchemaGraphEdge:
    """Schema 图中一条未经推导的有向关系模式。"""

    start_labels: tuple[str, ...]
    relationship_type: str
    end_labels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "start_labels",
            tuple(_require_text(label, "起点标签") for label in self.start_labels),
        )
        object.__setattr__(
            self,
            "relationship_type",
            _require_text(self.relationship_type, "关系类型"),
        )
        object.__setattr__(
            self,
            "end_labels",
            tuple(_require_text(label, "终点标签") for label in self.end_labels),
        )


@dataclass(frozen=True, slots=True)
class SchemaGraph:
    """由关系模式组成的轻量、有向且不可变的 Schema 图。"""

    nodes: tuple[str, ...] = ()
    edges: tuple[SchemaGraphEdge, ...] = ()
    outgoing: Mapping[str, tuple[SchemaGraphEdge, ...]] = field(
        default_factory=dict
    )
    incoming: Mapping[str, tuple[SchemaGraphEdge, ...]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "nodes",
            tuple(_require_text(node, "节点标签") for node in self.nodes),
        )
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(
            self,
            "outgoing",
            MappingProxyType(
                {
                    _require_text(label, "节点标签"): tuple(edges)
                    for label, edges in self.outgoing.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "incoming",
            MappingProxyType(
                {
                    _require_text(label, "节点标签"): tuple(edges)
                    for label, edges in self.incoming.items()
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class GraphSchema:
    """注入 Text2Cypher 提示词的结构化图谱 Schema。"""

    nodes: tuple[NodeSchema, ...] = ()
    relationships: tuple[RelationshipSchema, ...] = ()
    patterns: tuple[RelationshipPattern, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "relationships", tuple(self.relationships))
        object.__setattr__(self, "patterns", tuple(self.patterns))


@dataclass(frozen=True, slots=True)
class FewShotSchemaRequirements:
    """一条 Few-shot 示例适用所需的精确 Schema 子集。"""

    node_labels: tuple[str, ...] = ()
    relationship_types: tuple[str, ...] = ()
    node_properties: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    relationship_properties: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    patterns: tuple[RelationshipPattern, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "node_labels",
            tuple(_require_text(label, "节点标签") for label in self.node_labels),
        )
        object.__setattr__(
            self,
            "relationship_types",
            tuple(
                _require_text(relationship_type, "关系类型")
                for relationship_type in self.relationship_types
            ),
        )
        object.__setattr__(
            self,
            "node_properties",
            MappingProxyType(
                {
                    _require_text(label, "节点标签"): tuple(
                        _require_text(property_name, "节点属性名")
                        for property_name in properties
                    )
                    for label, properties in self.node_properties.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "relationship_properties",
            MappingProxyType(
                {
                    _require_text(relationship_type, "关系类型"): tuple(
                        _require_text(property_name, "关系属性名")
                        for property_name in properties
                    )
                    for relationship_type, properties
                    in self.relationship_properties.items()
                }
            ),
        )
        object.__setattr__(self, "patterns", tuple(self.patterns))


@dataclass(frozen=True, slots=True)
class FewShotExample:
    """一条自然语言到只读 Cypher 的黄金示例。"""

    id: str
    category: str
    question: str
    cypher: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    schema_requirements: FewShotSchemaRequirements

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text(self.id, "示例 ID"))
        object.__setattr__(
            self,
            "category",
            _require_text(self.category, "示例分类"),
        )
        object.__setattr__(
            self,
            "question",
            _require_text(self.question, "示例问题"),
        )
        object.__setattr__(
            self,
            "cypher",
            _require_text(self.cypher, "示例 Cypher"),
        )
        aliases = tuple(_require_text(alias, "示例别名") for alias in self.aliases)
        if not aliases:
            raise ValueError("示例别名不能为空")
        object.__setattr__(self, "aliases", aliases)
        tags = tuple(_require_text(tag, "示例标签") for tag in self.tags)
        if not tags:
            raise ValueError("示例标签不能为空")
        object.__setattr__(self, "tags", tags)


@dataclass(frozen=True, slots=True)
class QuestionDecomposition:
    """原始问题及其一到三个互相独立的子问题。"""

    original_question: str
    sub_questions: tuple[str, ...]

    def __post_init__(self) -> None:
        original_question = _require_text(self.original_question, "原始问题")
        sub_questions = tuple(
            _require_text(question, "子问题") for question in self.sub_questions
        )
        if not 1 <= len(sub_questions) <= 3:
            raise ValueError("子问题数量必须在 1 到 3 之间")
        if len(set(sub_questions)) != len(sub_questions):
            raise ValueError("子问题不能重复")
        if len(sub_questions) == 1 and sub_questions[0] != original_question:
            raise ValueError("未拆分的问题必须保留原始问题")
        object.__setattr__(self, "original_question", original_question)
        object.__setattr__(self, "sub_questions", sub_questions)

    @property
    def decomposed(self) -> bool:
        """问题是否被拆成了多个独立分支。"""

        return len(self.sub_questions) > 1


@dataclass(frozen=True, slots=True)
class ChatPrompt:
    """发送给聊天补全模型的系统消息和用户消息。"""

    system: str
    user: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "system", _require_text(self.system, "系统提示词"))
        object.__setattr__(self, "user", _require_text(self.user, "用户提示词"))


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """解析器所需的模型响应子集。"""

    content: str
    model: str | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """只读校验步骤完成后返回的证据。"""

    query_type: str
    notifications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "query_type",
            _require_text(self.query_type, "查询类型"),
        )
        object.__setattr__(self, "notifications", tuple(self.notifications))


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Neo4j 返回的、数量受限且 JSON 友好的记录集合。"""

    columns: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    truncated: bool = False
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(self.rows))
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("查询耗时不能为负数")


@dataclass(frozen=True, slots=True)
class SubQueryResponse:
    """一个子问题及其已执行的只读 Cypher 结果。"""

    question: str
    cypher: str
    result: QueryResult

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _require_text(self.question, "子问题"))
        object.__setattr__(self, "cypher", _require_text(self.cypher, "Cypher"))


@dataclass(frozen=True, slots=True)
class Text2CypherResponse:
    """一次成功 Text2Cypher 请求的公开结果。"""

    question: str
    sub_queries: tuple[SubQueryResponse, ...]
    formatted: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _require_text(self.question, "问题"))
        sub_queries = tuple(self.sub_queries)
        if not 1 <= len(sub_queries) <= 3:
            raise ValueError("子查询结果数量必须在 1 到 3 之间")
        object.__setattr__(self, "sub_queries", sub_queries)
        object.__setattr__(
            self,
            "formatted",
            _require_text(self.formatted, "格式化结果"),
        )

    @property
    def decomposed(self) -> bool:
        """是否包含多个子查询结果。"""

        return len(self.sub_queries) > 1
