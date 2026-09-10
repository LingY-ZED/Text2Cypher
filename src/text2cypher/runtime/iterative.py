"""CODEXGRAPH 风格的顺序迭代查询 Runtime。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from text2cypher.components.iterative_answerer import TemplateIterativeAnswerer
from text2cypher.domain.errors import QuestionValidationError
from text2cypher.domain.iterative import (
    ActionValue,
    EvidenceBinding,
    FindCallChainActionInput,
    IterativeAnswerContext,
    IterativePlan,
    IterativePlanningContext,
    IterativeRun,
    IterativeStopReason,
    KeyEntity,
    Observation,
    ObservationStatus,
    QueryCodeGraphActionInput,
    RawObservationResult,
    ResolveSymbolActionInput,
    RuntimeAction,
    RuntimeToolName,
)
from text2cypher.domain.models import (
    GraphQueryRequest,
    QueryContext,
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
)
from text2cypher.domain.ports import (
    CallChainLookupTool,
    CodeGraphQueryTool,
    IterativeAnswerer,
    IterativePlanner,
    SchemaTool,
    SymbolResolverTool,
)
from text2cypher.domain.query_shapes import QueryShape
from text2cypher.runtime.observations import ObservationCompactor


class BindingResolutionError(ValueError):
    """Action 的历史 Evidence Binding 无法安全解析。"""


class CapabilityUnavailableError(RuntimeError):
    """Planner 请求的确定性 Tool 未在当前 Runtime 中配置。"""


@dataclass(frozen=True, slots=True)
class _ResolvedAction:
    """已完成 Binding 的 Action；只在 Runtime 内部使用。"""

    fingerprint: str
    arguments: Mapping[str, Any]


class IterativeRuntime:
    """规划、顺序 Tool 执行、Observation 和充分性判断的外层循环。"""

    def __init__(
        self,
        *,
        schema_tool: SchemaTool,
        resolve_symbol_tool: SymbolResolverTool | None,
        call_chain_tool: CallChainLookupTool | None,
        query_code_graph_tool: CodeGraphQueryTool | None,
        planner: IterativePlanner,
        answerer: IterativeAnswerer,
        max_rounds: int = 3,
        max_actions: int = 9,
        max_consecutive_no_evidence_rounds: int = 2,
        observation_compactor: ObservationCompactor | None = None,
    ) -> None:
        self._validate_limits(
            max_rounds,
            max_actions,
            max_consecutive_no_evidence_rounds,
        )
        self._schema_tool = schema_tool
        self._resolve_symbol_tool = resolve_symbol_tool
        self._call_chain_tool = call_chain_tool
        self._query_code_graph_tool = query_code_graph_tool
        self._planner = planner
        self._answerer = answerer
        self._max_rounds = max_rounds
        self._max_actions = max_actions
        self._max_consecutive_no_evidence_rounds = (
            max_consecutive_no_evidence_rounds
        )
        self._observation_compactor = (
            observation_compactor or ObservationCompactor()
        )

    def run(self, question: str) -> IterativeRun:
        """运行受 Action/轮次/证据保护的迭代查询。"""

        normalized_question = self._normalize_question(question)
        schema = self._schema_tool.get_schema()
        query_context = QueryContext(schema)
        plans: list[IterativePlan] = []
        observations: list[Observation] = []
        known_fingerprints: set[str] = set()
        known_evidence: set[str] = set()
        obtained_information: tuple[str, ...] = ()
        missing_information: tuple[str, ...] = ()
        actions_consumed = 0
        rounds_executed = 0
        consecutive_no_evidence = 0
        stop_reason: IterativeStopReason | None = None

        while stop_reason is None:
            planning_context = self._planning_context(
                normalized_question,
                rounds_executed,
                actions_consumed,
                observations,
                obtained_information,
                missing_information,
            )
            try:
                plan = self._planner.plan(planning_context)
            except Exception:
                stop_reason = IterativeStopReason.PLANNER_FAILED
                missing_information = self._failure_missing_information(
                    missing_information,
                    "无法生成有效的下一轮查询计划",
                )
                break
            plans.append(plan)
            obtained_information = self._merge_information(
                obtained_information,
                plan.obtained_information,
            )
            missing_information = plan.missing_information
            if plan.decision.value == "complete":
                stop_reason = IterativeStopReason.COMPLETE
                break

            remaining_actions = self._max_actions - actions_consumed
            if remaining_actions <= 0:
                stop_reason = IterativeStopReason.ACTION_BUDGET_EXHAUSTED
                break
            actions = plan.actions[:remaining_actions]
            budget_truncated = len(actions) < len(plan.actions)
            round_observations: list[Observation] = []
            round_capability_failures = 0
            for action in actions:
                actions_consumed += 1
                observation, capability_missing = self._process_action(
                    action=action,
                    query_context=query_context,
                    prior_observations=observations,
                    known_fingerprints=known_fingerprints,
                    observation_id=f"o{len(observations) + 1}",
                )
                observations.append(observation)
                round_observations.append(observation)
                if capability_missing:
                    round_capability_failures += 1

            rounds_executed += 1
            round_evidence = {
                fingerprint
                for observation in round_observations
                for fingerprint in observation.evidence_fingerprints
            }
            if round_evidence.difference(known_evidence):
                known_evidence.update(round_evidence)
                consecutive_no_evidence = 0
            else:
                consecutive_no_evidence += 1

            if budget_truncated or actions_consumed >= self._max_actions:
                stop_reason = IterativeStopReason.ACTION_BUDGET_EXHAUSTED
            elif round_observations and all(
                observation.status is ObservationStatus.DUPLICATE
                for observation in round_observations
            ):
                stop_reason = IterativeStopReason.REPEATED_ACTIONS
            elif round_capability_failures == len(round_observations):
                stop_reason = IterativeStopReason.CAPABILITY_EXHAUSTED
            elif (
                consecutive_no_evidence
                >= self._max_consecutive_no_evidence_rounds
            ):
                stop_reason = IterativeStopReason.NO_NEW_EVIDENCE
            elif rounds_executed >= self._max_rounds:
                stop_reason = IterativeStopReason.MAX_ROUNDS

        assert stop_reason is not None
        summary = self._answer(
            normalized_question,
            observations,
            obtained_information,
            missing_information,
            stop_reason,
        )
        return IterativeRun(
            question=normalized_question,
            plans=tuple(plans),
            observations=tuple(observations),
            obtained_information=obtained_information,
            missing_information=missing_information,
            rounds_executed=rounds_executed,
            actions_consumed=actions_consumed,
            stop_reason=stop_reason,
            summary=summary,
        )

    def _process_action(
        self,
        *,
        action: RuntimeAction,
        query_context: QueryContext,
        prior_observations: list[Observation],
        known_fingerprints: set[str],
        observation_id: str,
    ) -> tuple[Observation, bool]:
        try:
            resolved = self._resolve_action(action, prior_observations)
        except Exception as error:
            return (
                self._observation_compactor.failed(
                    observation_id=observation_id,
                    action_id=action.action_id,
                    tool=action.tool,
                    error=error,
                ),
                False,
            )
        if resolved.fingerprint in known_fingerprints:
            return (
                self._observation_compactor.duplicate(
                    observation_id=observation_id,
                    action_id=action.action_id,
                    tool=action.tool,
                ),
                False,
            )
        known_fingerprints.add(resolved.fingerprint)
        try:
            raw_result = self._execute_action(action, resolved, query_context)
        except Exception as error:
            return (
                self._observation_compactor.failed(
                    observation_id=observation_id,
                    action_id=action.action_id,
                    tool=action.tool,
                    error=error,
                ),
                isinstance(error, CapabilityUnavailableError),
            )
        return (
            self._observation_compactor.result(
                observation_id=observation_id,
                action_id=action.action_id,
                tool=action.tool,
                raw_result=raw_result,
                action_fingerprint=resolved.fingerprint,
            ),
            False,
        )

    def _resolve_action(
        self,
        action: RuntimeAction,
        prior_observations: list[Observation],
    ) -> _ResolvedAction:
        entities = self._bound_entities(prior_observations)
        action_input = action.action_input
        arguments: dict[str, Any]
        if isinstance(action_input, ResolveSymbolActionInput):
            arguments = {
                "anchor": self._resolve_value(action_input.anchor, entities),
                "graph_version": self._resolve_optional_value(
                    action_input.graph_version, entities
                ),
                "service_name": self._resolve_optional_value(
                    action_input.service_name, entities
                ),
                "http_method": self._resolve_optional_value(
                    action_input.http_method, entities
                ),
                "api_path": self._resolve_optional_value(
                    action_input.api_path, entities
                ),
            }
        elif isinstance(action_input, FindCallChainActionInput):
            arguments = {
                "anchor": self._resolve_value(action_input.anchor, entities),
                "graph_version": self._resolve_optional_value(
                    action_input.graph_version, entities
                ),
                "local_hops": action_input.local_hops,
                "rest_hops": action_input.rest_hops,
                "mq_hops": action_input.mq_hops,
                "service_name": self._resolve_optional_value(
                    action_input.service_name, entities
                ),
                "http_method": self._resolve_optional_value(
                    action_input.http_method, entities
                ),
                "api_path": self._resolve_optional_value(
                    action_input.api_path, entities
                ),
            }
        elif isinstance(action_input, QueryCodeGraphActionInput):
            template_values = {
                name: self._resolve_binding(binding, entities)
                for name, binding in action_input.bindings.items()
            }
            try:
                question = action_input.question_template.format_map(template_values)
            except (KeyError, ValueError) as error:
                raise BindingResolutionError(
                    "question_template无法由Binding完整解析"
                ) from error
            arguments = {
                "question": self._normalize_question(question),
                "intent": action.intent,
                "required_information": action.expected_information,
                "anchor": self._resolve_optional_value(
                    action_input.anchor, entities
                ),
                "query_shape": (
                    None
                    if action_input.query_shape is None
                    else action_input.query_shape.value
                ),
            }
        else:
            raise TypeError("未知Action输入类型")
        return _ResolvedAction(
            fingerprint=self._action_fingerprint(action.tool, arguments),
            arguments=arguments,
        )

    def _execute_action(
        self,
        action: RuntimeAction,
        resolved: _ResolvedAction,
        query_context: QueryContext,
    ) -> RawObservationResult:
        arguments = resolved.arguments
        if action.tool is RuntimeToolName.RESOLVE_SYMBOL:
            resolver = self._resolve_symbol_tool
            if resolver is None:
                raise CapabilityUnavailableError("resolve_symbol不可用")
            return resolver.resolve_symbol(
                arguments["anchor"],
                graph_version=arguments["graph_version"],
                service_name=arguments["service_name"],
                http_method=arguments["http_method"],
                api_path=arguments["api_path"],
            )
        if action.tool is RuntimeToolName.FIND_CALL_CHAIN:
            call_chain_tool = self._call_chain_tool
            if call_chain_tool is None:
                raise CapabilityUnavailableError("find_call_chain不可用")
            return call_chain_tool.execute(
                arguments["anchor"],
                graph_version=arguments["graph_version"],
                local_hops=arguments["local_hops"],
                rest_hops=arguments["rest_hops"],
                mq_hops=arguments["mq_hops"],
                service_name=arguments["service_name"],
                http_method=arguments["http_method"],
                api_path=arguments["api_path"],
            )
        query_tool = self._query_code_graph_tool
        if query_tool is None:
            raise CapabilityUnavailableError("query_code_graph不可用")
        query_shape = arguments["query_shape"]
        return query_tool.query(
            GraphQueryRequest(
                query_id=action.action_id,
                question=arguments["question"],
                intent=arguments["intent"],
                required_information=arguments["required_information"],
                anchor=arguments["anchor"],
                query_shape=(
                    None if query_shape is None else QueryShape(query_shape)
                ),
            ),
            query_context,
        )

    @staticmethod
    def _bound_entities(
        observations: list[Observation],
    ) -> dict[str, KeyEntity]:
        return {
            entity.entity_id: entity
            for observation in observations
            if observation.status is ObservationStatus.SUCCESS
            for entity in observation.key_entities
        }

    @classmethod
    def _resolve_optional_value(
        cls,
        value: ActionValue | None,
        entities: Mapping[str, KeyEntity],
    ) -> str | None:
        return None if value is None else cls._resolve_value(value, entities)

    @classmethod
    def _resolve_value(
        cls,
        value: ActionValue,
        entities: Mapping[str, KeyEntity],
    ) -> str:
        if isinstance(value, EvidenceBinding):
            return cls._resolve_binding(value, entities)
        return value

    @staticmethod
    def _resolve_binding(
        binding: EvidenceBinding,
        entities: Mapping[str, KeyEntity],
    ) -> str:
        entity = entities.get(binding.entity_id)
        if entity is None:
            raise BindingResolutionError("Binding引用的实体不存在或尚未成功产生")
        value = entity.attributes.get(binding.field)
        if type(value) is not str or not value.strip():
            raise BindingResolutionError("Binding字段不存在或不是非空字符串")
        return value

    @staticmethod
    def _action_fingerprint(
        tool: RuntimeToolName,
        arguments: Mapping[str, Any],
    ) -> str:
        value = json.dumps(
            {"tool": tool.value, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def _planning_context(
        self,
        question: str,
        rounds_executed: int,
        actions_consumed: int,
        observations: list[Observation],
        obtained_information: tuple[str, ...],
        missing_information: tuple[str, ...],
    ) -> IterativePlanningContext:
        return IterativePlanningContext(
            original_question=question,
            next_round=rounds_executed + 1,
            remaining_rounds=self._max_rounds - rounds_executed,
            remaining_actions=self._max_actions - actions_consumed,
            observations=tuple(
                self._observation_compactor.planner_view(observation)
                for observation in observations
            ),
            obtained_information=obtained_information,
            missing_information=missing_information,
        )

    def _answer(
        self,
        question: str,
        observations: list[Observation],
        obtained_information: tuple[str, ...],
        missing_information: tuple[str, ...],
        stop_reason: IterativeStopReason,
    ) -> ResultSummary:
        context = IterativeAnswerContext(
            original_question=question,
            observations=tuple(
                self._observation_compactor.planner_view(observation)
                for observation in observations
            ),
            obtained_information=obtained_information,
            missing_information=missing_information,
            stop_reason=stop_reason,
        )
        try:
            return self._answerer.answer(context)
        except Exception:
            return ResultSummary(
                TemplateIterativeAnswerer().answer(context),
                ResultSummaryMode.TEMPLATE,
                ResultSummaryFallbackReason.LLM_FAILURE,
            )

    @staticmethod
    def _merge_information(
        existing: tuple[str, ...],
        incoming: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*existing, *incoming)))

    @staticmethod
    def _failure_missing_information(
        existing: tuple[str, ...],
        fallback: str,
    ) -> tuple[str, ...]:
        return existing or (fallback,)

    @staticmethod
    def _normalize_question(question: object) -> str:
        if type(question) is not str or not question.strip():
            raise QuestionValidationError("问题不能为空")
        return question.strip()

    @staticmethod
    def _validate_limits(
        max_rounds: object,
        max_actions: object,
        max_consecutive_no_evidence_rounds: object,
    ) -> None:
        for value, name in (
            (max_rounds, "max_rounds"),
            (max_actions, "max_actions"),
            (
                max_consecutive_no_evidence_rounds,
                "max_consecutive_no_evidence_rounds",
            ),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name}必须是正整数")
