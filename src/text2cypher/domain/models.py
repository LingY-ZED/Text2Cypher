"""领域层的不可变模型。"""

from __future__ import annotations

import re
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
    OUTPUT_CONTRACT = "output_contract"
    DEPENDENCY_PARAMETER = "dependency_parameter"


class SubQueryStatus(StrEnum):
    """一个计划节点在本次 DAG 调度中的最终状态。"""

    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


class Text2CypherStatus(StrEnum):
    """一次请求是否包含可返回的部分执行结果。"""

    SUCCESS = "success"
    PARTIAL = "partial"


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
class DependencyInput:
    """一个子问题从已完成父节点读取的结果列。"""

    source_id: str
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        source_id = _require_text(self.source_id, "依赖来源 ID")
        columns = tuple(
            _require_text(column, "依赖结果列") for column in self.columns
        )
        if not columns:
            raise ValueError("依赖结果列不能为空")
        if len(set(columns)) != len(columns):
            raise ValueError("依赖结果列不能重复")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "columns", columns)


@dataclass(frozen=True, slots=True)
class DependencyParameter:
    """向依赖子问题公开、但不包含实际值的 Neo4j 参数规格。"""

    name: str
    source_id: str
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        name = _require_text(self.name, "依赖参数名")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ValueError("依赖参数名必须是 ASCII 标识符")
        source_id = _require_text(self.source_id, "依赖来源 ID")
        columns = tuple(
            _require_text(column, "依赖参数列") for column in self.columns
        )
        if not columns:
            raise ValueError("依赖参数列不能为空")
        if len(set(columns)) != len(columns):
            raise ValueError("依赖参数列不能重复")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "columns", columns)


@dataclass(frozen=True, slots=True)
class SubQuestionPlan:
    """一个可独立执行或依赖父结果执行的子问题计划节点。"""

    id: str
    question: str
    inputs: tuple[DependencyInput, ...] = ()

    def __post_init__(self) -> None:
        identifier = _require_text(self.id, "子问题 ID")
        question = _require_text(self.question, "子问题")
        inputs = tuple(self.inputs)
        source_ids = tuple(item.source_id for item in inputs)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("子问题不能重复引用同一依赖来源")
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "question", question)
        object.__setattr__(self, "inputs", inputs)

    @property
    def depends_on(self) -> tuple[str, ...]:
        """按声明顺序返回当前节点的父节点 ID。"""

        return tuple(item.source_id for item in self.inputs)


@dataclass(frozen=True, slots=True)
class QuestionDecomposition:
    """原始问题及其一到三个、按稳定拓扑顺序排列的计划节点。"""

    original_question: str
    sub_questions: tuple[SubQuestionPlan, ...]

    def __post_init__(self) -> None:
        original_question = _require_text(self.original_question, "原始问题")
        sub_questions = tuple(self.sub_questions)
        if not 1 <= len(sub_questions) <= 3:
            raise ValueError("子问题数量必须在 1 到 3 之间")

        expected_ids = tuple(f"q{index}" for index in range(1, len(sub_questions) + 1))
        actual_ids = tuple(item.id for item in sub_questions)
        if actual_ids != expected_ids:
            raise ValueError("子问题 ID 必须按 q1、q2、q3 顺序编号")
        questions = tuple(item.question for item in sub_questions)
        if len(set(questions)) != len(questions):
            raise ValueError("子问题不能重复")

        seen_ids: set[str] = set()
        for item in sub_questions:
            if any(source_id not in seen_ids for source_id in item.depends_on):
                raise ValueError("依赖只能引用更早的子问题")
            seen_ids.add(item.id)

        if len(sub_questions) == 1:
            only_question = sub_questions[0]
            if only_question.question != original_question or only_question.inputs:
                raise ValueError("未拆分的问题必须保留原始问题且不得包含依赖")
        object.__setattr__(self, "original_question", original_question)
        object.__setattr__(self, "sub_questions", sub_questions)

    @property
    def decomposed(self) -> bool:
        """问题是否被拆成了多个计划节点。"""

        return len(self.sub_questions) > 1

    @classmethod
    def original(cls, question: str) -> QuestionDecomposition:
        """构造保留原始问题的单节点无依赖计划。"""

        normalized = _require_text(question, "原始问题")
        return cls(normalized, (SubQuestionPlan("q1", normalized),))

    @classmethod
    def independent(
        cls,
        original_question: str,
        questions: tuple[str, ...],
    ) -> QuestionDecomposition:
        """为测试或禁用依赖时构造一组互不依赖的计划节点。"""

        return cls(
            original_question,
            tuple(
                SubQuestionPlan(f"q{index}", question)
                for index, question in enumerate(questions, start=1)
            ),
        )


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
class SubQueryError:
    """可以安全公开的子查询失败或阻塞原因。"""

    kind: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _require_text(self.kind, "子查询错误类别"))
        object.__setattr__(
            self,
            "message",
            _require_text(self.message, "子查询错误信息"),
        )


@dataclass(frozen=True, slots=True)
class SubQueryResponse:
    """一个计划节点的成功、失败或依赖阻塞执行结果。"""

    question: str
    cypher: str | None
    result: QueryResult | None
    id: str = "q1"
    depends_on: tuple[str, ...] = ()
    parameter_sources: Mapping[str, DependencyParameter] = field(
        default_factory=dict
    )
    status: SubQueryStatus = SubQueryStatus.SUCCESS
    error: SubQueryError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _require_text(self.question, "子问题"))
        object.__setattr__(self, "id", _require_text(self.id, "子问题 ID"))
        depends_on = tuple(
            _require_text(source_id, "依赖来源 ID")
            for source_id in self.depends_on
        )
        if len(set(depends_on)) != len(depends_on):
            raise ValueError("依赖来源不能重复")
        parameter_sources = MappingProxyType(
            {
                _require_text(name, "依赖参数名"): specification
                for name, specification in self.parameter_sources.items()
            }
        )
        if set(parameter_sources) != {
            specification.name for specification in parameter_sources.values()
        }:
            raise ValueError("依赖参数来源键必须匹配参数名")
        if tuple(
            specification.source_id for specification in parameter_sources.values()
        ) != depends_on:
            raise ValueError("依赖参数来源必须与 depends_on 顺序一致")

        if self.status is SubQueryStatus.SUCCESS:
            if self.cypher is None or self.result is None or self.error is not None:
                raise ValueError("成功子查询必须包含 Cypher 和结果且不得包含错误")
            object.__setattr__(self, "cypher", _require_text(self.cypher, "Cypher"))
        else:
            if self.cypher is not None or self.result is not None or self.error is None:
                raise ValueError("失败或阻塞子查询只能包含安全错误")
        object.__setattr__(self, "depends_on", depends_on)
        object.__setattr__(self, "parameter_sources", parameter_sources)


@dataclass(frozen=True, slots=True)
class Text2CypherResponse:
    """一次成功或部分成功 Text2Cypher 请求的公开结果。"""

    question: str
    sub_queries: tuple[SubQueryResponse, ...]
    formatted: str
    status: Text2CypherStatus = Text2CypherStatus.SUCCESS

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _require_text(self.question, "问题"))
        sub_queries = tuple(self.sub_queries)
        if not 1 <= len(sub_queries) <= 3:
            raise ValueError("子查询结果数量必须在 1 到 3 之间")
        successful_count = sum(
            item.status is SubQueryStatus.SUCCESS for item in sub_queries
        )
        has_incomplete = successful_count != len(sub_queries)
        if self.status is Text2CypherStatus.SUCCESS and has_incomplete:
            raise ValueError("不完整子查询结果必须使用 partial 状态")
        if self.status is Text2CypherStatus.PARTIAL and not (
            successful_count and has_incomplete
        ):
            raise ValueError("partial 响应必须同时包含成功与未完成子查询")
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

    @property
    def successful_count(self) -> int:
        return sum(
            item.status is SubQueryStatus.SUCCESS for item in self.sub_queries
        )

    @property
    def failed_count(self) -> int:
        return sum(
            item.status is SubQueryStatus.FAILED for item in self.sub_queries
        )

    @property
    def blocked_count(self) -> int:
        return sum(
            item.status is SubQueryStatus.BLOCKED for item in self.sub_queries
        )
