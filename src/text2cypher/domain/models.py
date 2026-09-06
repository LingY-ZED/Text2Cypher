"""领域层的不可变模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from text2cypher.domain.query_shapes import QueryShape


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}不能为空")
    return normalized


def _require_strict_text(value: object, field_name: str) -> str:
    """拒绝隐式转换，保留规划契约的严格字符串边界。"""

    if type(value) is not str:
        raise TypeError(f"{field_name}必须是字符串")
    return _require_text(value, field_name)


def _validate_budget(value: object, field_name: str, *, maximum: int) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{field_name}必须是 0 到 {maximum} 的整数")


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
    query_shape: QueryShape = QueryShape.GENERAL

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
        if not isinstance(self.query_shape, QueryShape):
            raise TypeError("示例查询形状必须是 QueryShape")


@dataclass(frozen=True, slots=True)
class QueryShapeTemplate:
    """仅供 Translator 使用的、无业务实体值的查询结构模板。"""

    id: str
    query_shape: QueryShape
    template: str
    schema_requirements: FewShotSchemaRequirements

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text(self.id, "结构模板 ID"))
        if self.query_shape not in {
            QueryShape.FULL_ENTRY_CHAIN,
            QueryShape.FULL_DOWNSTREAM_CHAIN,
            QueryShape.FULL_METHOD_CALL_CHAIN,
        }:
            raise ValueError("结构模板只支持完整调用链形状")
        object.__setattr__(
            self,
            "template",
            _require_text(self.template, "查询结构模板"),
        )
        if not isinstance(
            self.schema_requirements,
            FewShotSchemaRequirements,
        ):
            raise TypeError("结构模板 Schema requirements 类型不合法")


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
class PrimaryAgentQuery:
    """Primary Agent 规划出的单条自然语言检索任务。"""

    query_id: str
    question: str
    intent: str
    required_information: tuple[str, ...]
    anchor: str | None = None
    query_shape: QueryShape | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "query_id",
            _require_strict_text(self.query_id, "查询 ID"),
        )
        object.__setattr__(
            self,
            "question",
            _require_strict_text(self.question, "规划子问题"),
        )
        object.__setattr__(
            self,
            "intent",
            _require_strict_text(self.intent, "检索意图"),
        )
        if type(self.required_information) is not tuple:
            raise TypeError("所需信息必须是元组")
        required_information = tuple(
            _require_strict_text(item, "所需信息")
            for item in self.required_information
        )
        if not required_information:
            raise ValueError("所需信息不能为空")
        if len(set(required_information)) != len(required_information):
            raise ValueError("所需信息不能重复")
        object.__setattr__(self, "required_information", required_information)
        if self.anchor is not None:
            object.__setattr__(
                self,
                "anchor",
                _require_strict_text(self.anchor, "查询锚点"),
            )
        if self.query_shape is not None and not isinstance(
            self.query_shape,
            QueryShape,
        ):
            raise TypeError("查询形状必须是 QueryShape")
        if (self.anchor is None) is not (self.query_shape is None):
            raise ValueError("查询锚点和查询形状必须同时提供或同时省略")

    @property
    def effective_query_shape(self) -> QueryShape:
        """优先使用 Primary 的显式规划，兼容旧计划时才做确定性解析。"""

        if self.query_shape is not None:
            return self.query_shape
        from text2cypher.domain.query_shapes import resolve_query_shape

        return resolve_query_shape(self.question)


@dataclass(frozen=True, slots=True)
class GraphQueryRequest:
    """Graph Query Engine 所需的一条独立查询任务。"""

    query_id: str
    question: str
    intent: str
    required_information: tuple[str, ...]
    anchor: str | None = None
    query_shape: QueryShape | None = None
    _primary_agent_query: PrimaryAgentQuery | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        primary_query = PrimaryAgentQuery(
            query_id=self.query_id,
            question=self.question,
            intent=self.intent,
            required_information=self.required_information,
            anchor=self.anchor,
            query_shape=self.query_shape,
        )
        object.__setattr__(self, "query_id", primary_query.query_id)
        object.__setattr__(self, "question", primary_query.question)
        object.__setattr__(self, "intent", primary_query.intent)
        object.__setattr__(
            self,
            "required_information",
            primary_query.required_information,
        )
        object.__setattr__(self, "anchor", primary_query.anchor)
        object.__setattr__(self, "query_shape", primary_query.query_shape)

    @classmethod
    def from_primary_agent_query(cls, query: PrimaryAgentQuery) -> GraphQueryRequest:
        """无损适配当前 Primary 计划，供兼容 Pipeline 使用。"""

        if not isinstance(query, PrimaryAgentQuery):
            raise TypeError("规划查询必须是 PrimaryAgentQuery")
        request = cls(
            query_id=query.query_id,
            question=query.question,
            intent=query.intent,
            required_information=query.required_information,
            anchor=query.anchor,
            query_shape=query.query_shape,
        )
        object.__setattr__(request, "_primary_agent_query", query)
        return request

    def as_primary_agent_query(self) -> PrimaryAgentQuery:
        """为保留现有 planned Router 和 PromptBuilder 行为提供兼容投影。"""

        if self._primary_agent_query is not None:
            return self._primary_agent_query
        return PrimaryAgentQuery(
            query_id=self.query_id,
            question=self.question,
            intent=self.intent,
            required_information=self.required_information,
            anchor=self.anchor,
            query_shape=self.query_shape,
        )


@dataclass(frozen=True, slots=True)
class QueryContext:
    """一次单轮查询共享的受信任图上下文。"""

    schema: GraphSchema

    def __post_init__(self) -> None:
        if not isinstance(self.schema, GraphSchema):
            raise TypeError("查询上下文必须包含 GraphSchema")


@dataclass(frozen=True, slots=True)
class QueryStatement:
    """确定性 Core 将参数与 Cypher 文本一起传递的不可变语句。"""

    cypher: str
    parameters: Mapping[str, str | int | float | bool | None] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "cypher", _require_strict_text(self.cypher, "Cypher"))
        if not isinstance(self.parameters, Mapping):
            raise TypeError("查询参数必须是映射")
        normalized: dict[str, str | int | float | bool | None] = {}
        for name, value in self.parameters.items():
            parameter_name = _require_strict_text(name, "查询参数名")
            if (
                not parameter_name.replace("_", "").isalnum()
                or parameter_name[0].isdigit()
            ):
                raise ValueError("查询参数名只能包含字母、数字和下划线")
            if value is not None and type(value) not in {str, int, float, bool}:
                raise TypeError("查询参数只支持标量或 null")
            normalized[parameter_name] = value
        object.__setattr__(self, "parameters", MappingProxyType(normalized))


@dataclass(frozen=True, slots=True)
class CallChainQuerySpec:
    """完整方法调用链编译器的结构化输入和固定查询预算。"""

    anchor_qualified_name: str
    additional_anchor_qualified_names: tuple[str, ...] = ()
    graph_version: str | None = None
    local_hops: int = 10
    rest_hops: int = 2
    mq_hops: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "anchor_qualified_name",
            _require_strict_text(self.anchor_qualified_name, "方法全限定名"),
        )
        if type(self.additional_anchor_qualified_names) is not tuple:
            raise TypeError("附加方法全限定名必须是元组")
        additional_names = tuple(
            _require_strict_text(value, "附加方法全限定名")
            for value in self.additional_anchor_qualified_names
        )
        if self.anchor_qualified_name in additional_names:
            raise ValueError("附加方法全限定名不能重复根方法全限定名")
        if len(set(additional_names)) != len(additional_names):
            raise ValueError("附加方法全限定名不能重复")
        object.__setattr__(
            self,
            "additional_anchor_qualified_names",
            additional_names,
        )
        if self.graph_version is not None:
            object.__setattr__(
                self,
                "graph_version",
                _require_strict_text(self.graph_version, "图谱版本"),
            )
        _validate_budget(self.local_hops, "服务内方法跳数", maximum=10)
        _validate_budget(self.rest_hops, "REST 跨服务层数", maximum=2)
        _validate_budget(self.mq_hops, "MQ 发布消费层数", maximum=1)

    @property
    def anchor_qualified_names(self) -> tuple[str, ...]:
        """返回稳定去重后的全部方法锚点，供同名方法的单语句查询使用。"""

        return (
            self.anchor_qualified_name,
            *self.additional_anchor_qualified_names,
        )


@dataclass(frozen=True, slots=True)
class ResolvedMethod:
    """实体解析后可作为确定性查询锚点的方法标识。"""

    qualified_name: str
    method_name: str
    class_qualified_name: str
    service_name: str
    graph_version: str

    def __post_init__(self) -> None:
        for name in (
            "qualified_name",
            "method_name",
            "class_qualified_name",
            "service_name",
            "graph_version",
        ):
            object.__setattr__(
                self,
                name,
                _require_strict_text(getattr(self, name), name),
            )


class CallChainLinkType(StrEnum):
    """完整调用链稳定表格中的物理分段类型。"""

    LOCAL = "local"
    REST = "rest"
    MQ = "mq"


CALL_CHAIN_RESULT_COLUMNS = (
    "根方法",
    "图谱版本",
    "层级",
    "链路类型",
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
    "消息交换机",
    "消息队列",
    "路由键",
)


@dataclass(frozen=True, slots=True)
class CallChainSegment:
    """完整方法调用链表格的一行，不携带 Neo4j Node 或 Path 对象。"""

    root_method: str
    graph_version: str
    level: int
    link_type: CallChainLinkType
    source_service: str | None
    source_api_path: str | None
    source_http_method: str | None
    source_method: str | None
    method_path: tuple[str, ...]
    downstream_api_path: str | None
    target_service: str | None
    target_api_path: str | None
    target_http_method: str | None
    target_method: str | None
    message_exchange: str | None
    message_queue: str | None
    routing_key: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "root_method",
            _require_strict_text(self.root_method, "根方法"),
        )
        object.__setattr__(
            self,
            "graph_version",
            _require_strict_text(self.graph_version, "图谱版本"),
        )
        if type(self.level) is not int or self.level < 0:
            raise ValueError("层级必须是非负整数")
        if not isinstance(self.link_type, CallChainLinkType):
            raise TypeError("链路类型必须是 CallChainLinkType")
        for name in (
            "source_service",
            "source_api_path",
            "source_http_method",
            "source_method",
            "downstream_api_path",
            "target_service",
            "target_api_path",
            "target_http_method",
            "target_method",
            "message_exchange",
            "message_queue",
            "routing_key",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_strict_text(value, name))
        if type(self.method_path) is not tuple or not self.method_path:
            raise ValueError("方法路径必须是非空元组")
        object.__setattr__(
            self,
            "method_path",
            tuple(
                _require_strict_text(value, "方法路径")
                for value in self.method_path
            ),
        )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> CallChainSegment:
        """从固定表格契约的行还原为类型化调用链分段。"""

        if not isinstance(row, Mapping):
            raise TypeError("调用链结果行必须是映射")
        if set(row) != set(CALL_CHAIN_RESULT_COLUMNS):
            raise ValueError("调用链结果行必须包含完整且唯一的表格列")
        method_path = row["方法路径"]
        if not isinstance(method_path, (list, tuple)):
            raise TypeError("方法路径必须是列表或元组")
        try:
            link_type = CallChainLinkType(row["链路类型"])
        except (TypeError, ValueError) as error:
            raise ValueError("链路类型必须是 local、rest 或 mq") from error
        return cls(
            root_method=row["根方法"],
            graph_version=row["图谱版本"],
            level=row["层级"],
            link_type=link_type,
            source_service=row["源服务"],
            source_api_path=row["源API路径"],
            source_http_method=row["源HTTP方法"],
            source_method=row["源方法"],
            method_path=tuple(method_path),
            downstream_api_path=row["下游API路径"],
            target_service=row["目标服务"],
            target_api_path=row["目标API路径"],
            target_http_method=row["目标HTTP方法"],
            target_method=row["目标方法"],
            message_exchange=row["消息交换机"],
            message_queue=row["消息队列"],
            routing_key=row["路由键"],
        )

    def to_row(self) -> dict[str, str | int | list[str] | None]:
        """投影为公共接口可直接 JSON 序列化的稳定表格行。"""

        return {
            "根方法": self.root_method,
            "图谱版本": self.graph_version,
            "层级": self.level,
            "链路类型": self.link_type.value,
            "源服务": self.source_service,
            "源API路径": self.source_api_path,
            "源HTTP方法": self.source_http_method,
            "源方法": self.source_method,
            "方法路径": list(self.method_path),
            "下游API路径": self.downstream_api_path,
            "目标服务": self.target_service,
            "目标API路径": self.target_api_path,
            "目标HTTP方法": self.target_http_method,
            "目标方法": self.target_method,
            "消息交换机": self.message_exchange,
            "消息队列": self.message_queue,
            "路由键": self.routing_key,
        }


@dataclass(frozen=True, slots=True)
class PrimaryAgentPlan:
    """Primary Agent 的可审计、单轮自然语言检索计划。"""

    original_question: str
    analysis_summary: str
    queries: tuple[PrimaryAgentQuery, ...]

    def __post_init__(self) -> None:
        original_question = _require_strict_text(self.original_question, "原始问题")
        analysis_summary = _require_strict_text(self.analysis_summary, "分析摘要")
        if type(self.queries) is not tuple:
            raise TypeError("规划查询必须是元组")
        queries = tuple(self.queries)
        if not 1 <= len(queries) <= 3:
            raise ValueError("规划查询数量必须在 1 到 3 之间")
        if any(not isinstance(query, PrimaryAgentQuery) for query in queries):
            raise TypeError("规划查询必须是 PrimaryAgentQuery")
        expected_ids = tuple(f"q{index}" for index in range(1, len(queries) + 1))
        if tuple(query.query_id for query in queries) != expected_ids:
            raise ValueError("查询 ID 必须从 q1 起连续编号")
        questions = tuple(query.question for query in queries)
        if len(set(questions)) != len(questions):
            raise ValueError("规划子问题不能重复")
        if len(queries) == 1 and queries[0].question != original_question:
            raise ValueError("单查询计划必须保留原始问题")
        object.__setattr__(self, "original_question", original_question)
        object.__setattr__(self, "analysis_summary", analysis_summary)
        object.__setattr__(self, "queries", queries)

    @classmethod
    def fallback(cls, question: str) -> PrimaryAgentPlan:
        """构造不依赖 LLM 的原问题单查询计划。"""

        original_question = _require_strict_text(question, "原始问题")
        return cls(
            original_question=original_question,
            analysis_summary="使用原始问题执行单次检索",
            queries=(
                PrimaryAgentQuery(
                    query_id="q1",
                    question=original_question,
                    intent="回答原始问题",
                    required_information=("回答原始问题所需的图数据",),
                ),
            ),
        )

    @property
    def decomposed(self) -> bool:
        """计划是否包含多个互相独立的查询。"""

        return len(self.queries) > 1

    @property
    def sub_questions(self) -> tuple[str, ...]:
        """向兼容层提供计划内自然语言子问题。"""

        return tuple(query.question for query in self.queries)


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
class ExecutedCypher:
    """已通过只读准入并执行完成的一条 Cypher 及其结构化结果。"""

    cypher: str
    result: QueryResult

    def __post_init__(self) -> None:
        object.__setattr__(self, "cypher", _require_text(self.cypher, "Cypher"))


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
class SingleRoundRun:
    """单轮 Runtime 的结构化执行结果，不包含接口展示文本。"""

    question: str
    plan: PrimaryAgentPlan
    sub_queries: tuple[SubQueryResponse, ...]
    summary: ResultSummary | None = None

    def __post_init__(self) -> None:
        question = _require_text(self.question, "问题")
        if not isinstance(self.plan, PrimaryAgentPlan):
            raise TypeError("单轮运行计划必须是 PrimaryAgentPlan")
        sub_queries = tuple(self.sub_queries)
        if not 1 <= len(sub_queries) <= 3:
            raise ValueError("子查询结果数量必须在 1 到 3 之间")
        if any(
            not isinstance(sub_query, SubQueryResponse)
            for sub_query in sub_queries
        ):
            raise TypeError("子查询结果必须是 SubQueryResponse")
        if self.plan.original_question != question:
            raise ValueError("运行问题必须与计划原始问题一致")
        if tuple(query.question for query in self.plan.queries) != tuple(
            sub_query.question for sub_query in sub_queries
        ):
            raise ValueError("子查询结果必须与计划顺序一致")
        if self.summary is not None and not isinstance(self.summary, ResultSummary):
            raise TypeError("自然语言总结必须是 ResultSummary 或 None")
        object.__setattr__(self, "question", question)
        object.__setattr__(self, "sub_queries", sub_queries)


@dataclass(frozen=True, slots=True)
class Text2CypherResponse:
    """一次成功 Text2Cypher 请求的公开结果。"""

    question: str
    sub_queries: tuple[SubQueryResponse, ...]
    formatted: str
    summary: ResultSummary | None = None

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
        if self.summary is not None and not isinstance(self.summary, ResultSummary):
            raise TypeError("自然语言总结必须是 ResultSummary 或 None")

    @property
    def decomposed(self) -> bool:
        """是否包含多个子查询结果。"""

        return len(self.sub_queries) > 1
