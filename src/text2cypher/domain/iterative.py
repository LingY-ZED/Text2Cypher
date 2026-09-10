"""迭代 Agent Runtime 的稳定领域契约。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from text2cypher.domain.models import ExecutedCypher, ResolvedMethod, ResultSummary
from text2cypher.domain.query_shapes import QueryShape


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name}必须是非空字符串")
    return value.strip()


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name}必须是正整数")
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name}必须是非负整数")
    return value


def _texts(values: object, field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise TypeError(f"{field_name}必须是元组")
    normalized = tuple(_text(item, field_name) for item in values)
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name}不能为空")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name}不能重复")
    return normalized


class PlanDecision(StrEnum):
    """Planner 对当前证据充分性的判断。"""

    CONTINUE = "continue"
    COMPLETE = "complete"


class RuntimeToolName(StrEnum):
    """首版 IterativeRuntime 可调用的封闭 Tool 集合。"""

    RESOLVE_SYMBOL = "resolve_symbol"
    FIND_CALL_CHAIN = "find_call_chain"
    QUERY_CODE_GRAPH = "query_code_graph"


class ObservationStatus(StrEnum):
    """一次 Action 的可审计结果状态。"""

    SUCCESS = "success"
    EMPTY = "empty"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class IterativeStopReason(StrEnum):
    """IterativeRuntime 的固定终止原因。"""

    COMPLETE = "complete"
    MAX_ROUNDS = "max_rounds"
    ACTION_BUDGET_EXHAUSTED = "action_budget_exhausted"
    REPEATED_ACTIONS = "repeated_actions"
    NO_NEW_EVIDENCE = "no_new_evidence"
    PLANNER_FAILED = "planner_failed"
    CAPABILITY_EXHAUSTED = "capability_exhausted"


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """从历史 KeyEntity 的一个公开字段读取标量。"""

    entity_id: str
    field: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _text(self.entity_id, "entity_id"))
        object.__setattr__(self, "field", _text(self.field, "field"))


type ActionValue = str | EvidenceBinding


@dataclass(frozen=True, slots=True)
class ResolveSymbolActionInput:
    """确定性方法实体解析参数。"""

    anchor: ActionValue
    graph_version: ActionValue | None = None
    service_name: ActionValue | None = None
    http_method: ActionValue | None = None
    api_path: ActionValue | None = None

    def __post_init__(self) -> None:
        _validate_action_values(self)


@dataclass(frozen=True, slots=True)
class FindCallChainActionInput:
    """确定性完整调用链查询参数。"""

    anchor: ActionValue
    graph_version: ActionValue | None = None
    local_hops: int = 10
    rest_hops: int = 2
    mq_hops: int = 1
    service_name: ActionValue | None = None
    http_method: ActionValue | None = None
    api_path: ActionValue | None = None

    def __post_init__(self) -> None:
        _validate_action_values(self)
        _positive_int(self.local_hops, "local_hops")
        _positive_int(self.rest_hops, "rest_hops")
        _positive_int(self.mq_hops, "mq_hops")


@dataclass(frozen=True, slots=True)
class QueryCodeGraphActionInput:
    """通用图查询参数；模板变量只能来自显式 Evidence Binding。"""

    question_template: str
    bindings: Mapping[str, EvidenceBinding] = field(default_factory=dict)
    anchor: ActionValue | None = None
    query_shape: QueryShape | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "question_template",
            _text(self.question_template, "question_template"),
        )
        if not isinstance(self.bindings, Mapping):
            raise TypeError("bindings必须是映射")
        normalized: dict[str, EvidenceBinding] = {}
        for name, binding in self.bindings.items():
            key = _text(name, "binding名称")
            if not key.replace("_", "a").isalnum() or key[0].isdigit():
                raise ValueError("binding名称只能包含字母、数字和下划线")
            if not isinstance(binding, EvidenceBinding):
                raise TypeError("binding值必须是EvidenceBinding")
            normalized[key] = binding
        object.__setattr__(self, "bindings", MappingProxyType(normalized))
        if self.anchor is not None:
            _validate_action_value(self.anchor, "anchor")
        if self.query_shape is not None and not isinstance(
            self.query_shape, QueryShape
        ):
            raise TypeError("query_shape必须是QueryShape或None")
        if self.query_shape is QueryShape.FULL_METHOD_CALL_CHAIN:
            raise ValueError("完整调用链必须使用find_call_chain Tool")


type RuntimeActionInput = (
    ResolveSymbolActionInput
    | FindCallChainActionInput
    | QueryCodeGraphActionInput
)


@dataclass(frozen=True, slots=True)
class RuntimeAction:
    """Planner 产生的一条类型化、可绑定的 Tool 调用。"""

    action_id: str
    tool: RuntimeToolName
    intent: str
    expected_information: tuple[str, ...]
    action_input: RuntimeActionInput

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", _text(self.action_id, "action_id"))
        if not isinstance(self.tool, RuntimeToolName):
            raise TypeError("tool必须是RuntimeToolName")
        object.__setattr__(self, "intent", _text(self.intent, "intent"))
        object.__setattr__(
            self,
            "expected_information",
            _texts(
                self.expected_information,
                "expected_information",
                allow_empty=False,
            ),
        )
        expected_type = {
            RuntimeToolName.RESOLVE_SYMBOL: ResolveSymbolActionInput,
            RuntimeToolName.FIND_CALL_CHAIN: FindCallChainActionInput,
            RuntimeToolName.QUERY_CODE_GRAPH: QueryCodeGraphActionInput,
        }[self.tool]
        if not isinstance(self.action_input, expected_type):
            raise TypeError("Tool与Action输入类型不匹配")


@dataclass(frozen=True, slots=True)
class IterativePlan:
    """一次充分性判断和下一批 Action。"""

    decision: PlanDecision
    obtained_information: tuple[str, ...]
    missing_information: tuple[str, ...]
    actions: tuple[RuntimeAction, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.decision, PlanDecision):
            raise TypeError("decision必须是PlanDecision")
        object.__setattr__(
            self,
            "obtained_information",
            _texts(
                self.obtained_information,
                "obtained_information",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "missing_information",
            _texts(
                self.missing_information,
                "missing_information",
                allow_empty=True,
            ),
        )
        if type(self.actions) is not tuple:
            raise TypeError("actions必须是元组")
        actions = tuple(self.actions)
        if any(not isinstance(action, RuntimeAction) for action in actions):
            raise TypeError("actions必须只包含RuntimeAction")
        if len({action.action_id for action in actions}) != len(actions):
            raise ValueError("Action ID不能重复")
        if self.decision is PlanDecision.COMPLETE:
            if self.missing_information or actions:
                raise ValueError("complete计划不能包含缺失信息或Action")
        elif not self.missing_information or not 1 <= len(actions) <= 3:
            raise ValueError("continue计划必须包含缺失信息和1到3个Action")
        object.__setattr__(self, "actions", actions)


@dataclass(frozen=True, slots=True)
class KeyEntity:
    """可安全提供给 Planner 并用于后续 Binding 的关键实体。"""

    entity_id: str
    kind: str
    attributes: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_id", _text(self.entity_id, "entity_id"))
        object.__setattr__(self, "kind", _text(self.kind, "kind"))
        if not isinstance(self.attributes, Mapping) or not self.attributes:
            raise ValueError("attributes必须是非空映射")
        normalized = {
            _text(name, "实体字段名"): _freeze_value(value)
            for name, value in self.attributes.items()
        }
        object.__setattr__(self, "attributes", MappingProxyType(normalized))


type RawObservationResult = (
    ExecutedCypher | tuple[ResolvedMethod, ...] | None
)


@dataclass(frozen=True, slots=True)
class Observation:
    """保留原始结果、但为 Planner 提供紧凑投影的一次 Action 结果。"""

    observation_id: str
    action_id: str
    tool: RuntimeToolName
    status: ObservationStatus
    summary: str
    row_count: int
    columns: tuple[str, ...]
    truncated: bool
    key_entities: tuple[KeyEntity, ...]
    evidence_fingerprints: tuple[str, ...]
    raw_result: RawObservationResult = field(repr=False)
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _text(self.observation_id, "observation_id")
        )
        object.__setattr__(self, "action_id", _text(self.action_id, "action_id"))
        if not isinstance(self.tool, RuntimeToolName):
            raise TypeError("tool必须是RuntimeToolName")
        if not isinstance(self.status, ObservationStatus):
            raise TypeError("status必须是ObservationStatus")
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        _non_negative_int(self.row_count, "row_count")
        object.__setattr__(
            self, "columns", _texts(self.columns, "columns", allow_empty=True)
        )
        if type(self.truncated) is not bool:
            raise TypeError("truncated必须是bool")
        if type(self.key_entities) is not tuple or any(
            not isinstance(entity, KeyEntity) for entity in self.key_entities
        ):
            raise TypeError("key_entities必须是KeyEntity元组")
        object.__setattr__(
            self,
            "evidence_fingerprints",
            _texts(
                self.evidence_fingerprints,
                "evidence_fingerprints",
                allow_empty=True,
            ),
        )
        if self.error_type is not None:
            object.__setattr__(
                self, "error_type", _text(self.error_type, "error_type")
            )
        if self.error_message is not None:
            object.__setattr__(
                self, "error_message", _text(self.error_message, "error_message")
            )
        has_error = self.error_type is not None or self.error_message is not None
        if self.status is ObservationStatus.FAILED and not has_error:
            raise ValueError("failed Observation必须包含错误信息")
        if self.status is not ObservationStatus.FAILED and has_error:
            raise ValueError("只有failed Observation可以包含错误信息")


@dataclass(frozen=True, slots=True)
class PlannerObservation:
    """Observation 的 LLM 安全紧凑视图。"""

    observation_id: str
    action_id: str
    tool: RuntimeToolName
    status: ObservationStatus
    summary: str
    row_count: int
    columns: tuple[str, ...]
    truncated: bool
    key_entities: tuple[KeyEntity, ...]
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _text(self.observation_id, "observation_id")
        )
        object.__setattr__(self, "action_id", _text(self.action_id, "action_id"))
        if not isinstance(self.tool, RuntimeToolName):
            raise TypeError("tool必须是RuntimeToolName")
        if not isinstance(self.status, ObservationStatus):
            raise TypeError("status必须是ObservationStatus")
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        _non_negative_int(self.row_count, "row_count")
        object.__setattr__(
            self, "columns", _texts(self.columns, "columns", allow_empty=True)
        )
        if type(self.truncated) is not bool:
            raise TypeError("truncated必须是bool")
        if type(self.key_entities) is not tuple or any(
            not isinstance(entity, KeyEntity) for entity in self.key_entities
        ):
            raise TypeError("key_entities必须是KeyEntity元组")
        if self.error_type is not None:
            object.__setattr__(
                self, "error_type", _text(self.error_type, "error_type")
            )
        if self.error_message is not None:
            object.__setattr__(
                self, "error_message", _text(self.error_message, "error_message")
            )


@dataclass(frozen=True, slots=True)
class IterativePlanningContext:
    """一次 Planner 调用可见的完整、紧凑运行状态。"""

    original_question: str
    next_round: int
    remaining_rounds: int
    remaining_actions: int
    observations: tuple[PlannerObservation, ...]
    obtained_information: tuple[str, ...]
    missing_information: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "original_question",
            _text(self.original_question, "original_question"),
        )
        _positive_int(self.next_round, "next_round")
        _non_negative_int(self.remaining_rounds, "remaining_rounds")
        _non_negative_int(self.remaining_actions, "remaining_actions")
        if type(self.observations) is not tuple or any(
            not isinstance(item, PlannerObservation) for item in self.observations
        ):
            raise TypeError("observations必须是PlannerObservation元组")
        object.__setattr__(
            self,
            "obtained_information",
            _texts(
                self.obtained_information,
                "obtained_information",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "missing_information",
            _texts(
                self.missing_information,
                "missing_information",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class IterativeAnswerContext:
    """Answerer 可见的紧凑终态。"""

    original_question: str
    observations: tuple[PlannerObservation, ...]
    obtained_information: tuple[str, ...]
    missing_information: tuple[str, ...]
    stop_reason: IterativeStopReason

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "original_question",
            _text(self.original_question, "original_question"),
        )
        if not isinstance(self.stop_reason, IterativeStopReason):
            raise TypeError("stop_reason必须是IterativeStopReason")
        if type(self.observations) is not tuple or any(
            not isinstance(item, PlannerObservation) for item in self.observations
        ):
            raise TypeError("observations必须是PlannerObservation元组")
        object.__setattr__(
            self,
            "obtained_information",
            _texts(
                self.obtained_information,
                "obtained_information",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "missing_information",
            _texts(
                self.missing_information,
                "missing_information",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class IterativeRun:
    """IterativeRuntime 的完整结构化结果。"""

    question: str
    plans: tuple[IterativePlan, ...]
    observations: tuple[Observation, ...]
    obtained_information: tuple[str, ...]
    missing_information: tuple[str, ...]
    rounds_executed: int
    actions_consumed: int
    stop_reason: IterativeStopReason
    summary: ResultSummary

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _text(self.question, "question"))
        if type(self.plans) is not tuple or any(
            not isinstance(plan, IterativePlan) for plan in self.plans
        ):
            raise TypeError("plans必须是IterativePlan元组")
        if type(self.observations) is not tuple or any(
            not isinstance(item, Observation) for item in self.observations
        ):
            raise TypeError("observations必须是Observation元组")
        object.__setattr__(
            self,
            "obtained_information",
            _texts(
                self.obtained_information,
                "obtained_information",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "missing_information",
            _texts(
                self.missing_information,
                "missing_information",
                allow_empty=True,
            ),
        )
        _non_negative_int(self.rounds_executed, "rounds_executed")
        _non_negative_int(self.actions_consumed, "actions_consumed")
        if not isinstance(self.stop_reason, IterativeStopReason):
            raise TypeError("stop_reason必须是IterativeStopReason")
        if not isinstance(self.summary, ResultSummary):
            raise TypeError("summary必须是ResultSummary")


def _validate_action_value(value: object, field_name: str) -> None:
    if isinstance(value, EvidenceBinding):
        return
    _text(value, field_name)


def _validate_action_values(
    value: ResolveSymbolActionInput | FindCallChainActionInput,
) -> None:
    for field_name in (
        "anchor",
        "graph_version",
        "service_name",
        "http_method",
        "api_path",
    ):
        field_value = getattr(value, field_name)
        if field_value is not None:
            _validate_action_value(field_value, field_name)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                _text(key, "实体字段名"): _freeze_value(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if value is None or type(value) in {str, int, float, bool}:
        return value
    return str(value)
