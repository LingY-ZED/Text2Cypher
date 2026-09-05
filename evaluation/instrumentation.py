"""Evaluation-only decorators that observe ports without changing production APIs."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    GraphSchema,
    LLMResponse,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
    QueryResult,
    ValidationReport,
)
from text2cypher.domain.ports import (
    CypherExecutor,
    CypherParser,
    CypherValidator,
    FewShotRouter,
    LLMClient,
    PlannedFewShotRouter,
    PrimaryAgent,
)
from text2cypher.domain.query_shapes import resolve_query_shape


@dataclass(slots=True)
class EvaluationRecorder:
    """Mutable event sink scoped to one sequential evaluation process."""

    events: list[dict[str, Any]] = field(default_factory=list)
    recovery_events: list[dict[str, Any]] = field(default_factory=list)
    last_candidate_stage: str | None = None
    last_parsed_stage: str | None = None
    current_query_index: int = 0

    def reset(self) -> None:
        self.events.clear()
        self.recovery_events.clear()
        self.last_candidate_stage = None
        self.last_parsed_stage = None
        self.current_query_index = 0

    def add(self, **event: Any) -> None:
        self.events.append(event)


class StageLLMClient:
    """Label calls made through a shared production LLM client."""

    def __init__(
        self,
        delegate: LLMClient,
        recorder: EvaluationRecorder,
        stage: str,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder
        self._stage = stage

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        if self._stage == "generation":
            self._recorder.current_query_index += 1
        started = perf_counter()
        try:
            response = self._delegate.generate(prompt)
        except Exception as error:
            self._recorder.add(
                component="llm",
                stage=self._stage,
                outcome="failed",
                duration_seconds=round(perf_counter() - started, 6),
                error_type=type(error).__name__,
                query_index=self._recorder.current_query_index,
            )
            raise
        self._recorder.last_candidate_stage = self._stage
        self._recorder.add(
            component="llm",
            stage=self._stage,
            outcome="succeeded",
            duration_seconds=round(perf_counter() - started, 6),
            content_length=len(response.content),
            prompt_system_length=len(prompt.system),
            prompt_user_length=len(prompt.user),
            model=response.model,
            finish_reason=response.finish_reason,
            query_index=self._recorder.current_query_index,
        )
        return response


class RecordingFewShotRouter:
    """Record selected IDs without retaining router prompts or response bodies."""

    def __init__(
        self,
        delegate: FewShotRouter,
        recorder: EvaluationRecorder,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def route(
        self,
        question: str,
        schema: GraphSchema,
    ) -> tuple[FewShotExample, ...]:
        selected = self._delegate.route(question, schema)
        self._record(question, resolve_query_shape(question).value, selected)
        return selected

    def route_planned(
        self,
        query: PrimaryAgentQuery,
        schema: GraphSchema,
    ) -> tuple[FewShotExample, ...]:
        if isinstance(self._delegate, PlannedFewShotRouter):
            selected = self._delegate.route_planned(query, schema)
        else:
            selected = self._delegate.route(query.question, schema)
        self._record(
            query.question,
            query.effective_query_shape.value,
            selected,
        )
        return selected

    def _record(
        self,
        question: str,
        effective_query_shape: str,
        selected: tuple[FewShotExample, ...],
    ) -> None:
        self._recorder.add(
            component="router",
            stage="selection",
            outcome="succeeded",
            question=question,
            effective_query_shape=effective_query_shape,
            selected_ids=[example.id for example in selected],
            query_index=self._recorder.current_query_index + 1,
        )


class RecordingPrimaryAgent:
    """Record the internal Primary Agent plan for evaluation-only diagnosis."""

    def __init__(
        self,
        delegate: PrimaryAgent,
        recorder: EvaluationRecorder,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def plan(self, question: str) -> PrimaryAgentPlan:
        result = self._delegate.plan(question)
        self._recorder.add(
            component="primary_agent",
            stage="plan_result",
            outcome="succeeded",
            decomposed=result.decomposed,
            query_count=len(result.queries),
            analysis_summary=result.analysis_summary,
            queries=[
                {
                    "query_id": query.query_id,
                    "question": query.question,
                    "intent": query.intent,
                    "required_information": list(query.required_information),
                    "anchor": query.anchor,
                    "query_shape": (
                        query.query_shape.value
                        if query.query_shape is not None
                        else None
                    ),
                }
                for query in result.queries
            ],
        )
        return result


class RecordingCypherParser:
    """Record normalized candidates and parse failures."""

    def __init__(
        self,
        delegate: CypherParser,
        recorder: EvaluationRecorder,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def parse(self, text: str) -> str:
        source = self._recorder.last_candidate_stage or "unknown"
        try:
            cypher = self._delegate.parse(text)
        except Exception as error:
            self._recorder.add(
                component="cypher",
                stage="parse",
                source=source,
                outcome="failed",
                error_type=type(error).__name__,
                candidate_length=len(text),
                query_index=self._recorder.current_query_index,
            )
            raise
        self._recorder.last_parsed_stage = source
        self._recorder.add(
            component="cypher",
            stage="parse",
            source=source,
            outcome="succeeded",
            cypher=cypher,
            query_index=self._recorder.current_query_index,
        )
        return cypher


class RecordingCypherValidator:
    """Record EXPLAIN outcomes for initial and corrected Cypher."""

    def __init__(
        self,
        delegate: CypherValidator,
        recorder: EvaluationRecorder,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def validate(self, cypher: str) -> ValidationReport:
        source = self._recorder.last_parsed_stage or "unknown"
        started = perf_counter()
        try:
            report = self._delegate.validate(cypher)
        except Exception as error:
            self._recorder.add(
                component="neo4j",
                stage="explain",
                source=source,
                outcome="failed",
                duration_seconds=round(perf_counter() - started, 6),
                error_type=type(error).__name__,
                query_index=self._recorder.current_query_index,
            )
            raise
        self._recorder.add(
            component="neo4j",
            stage="explain",
            source=source,
            outcome="succeeded",
            duration_seconds=round(perf_counter() - started, 6),
            query_type=report.query_type,
            notifications=list(report.notifications),
            query_index=self._recorder.current_query_index,
        )
        return report


class RecordingCypherExecutor:
    """Record execution outcomes without duplicating unbounded result data."""

    def __init__(
        self,
        delegate: CypherExecutor,
        recorder: EvaluationRecorder,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult:
        source = self._recorder.last_parsed_stage or "unknown"
        started = perf_counter()
        try:
            result = self._delegate.execute(cypher, parameters)
        except Exception as error:
            self._recorder.add(
                component="neo4j",
                stage="execute",
                source=source,
                outcome="failed",
                duration_seconds=round(perf_counter() - started, 6),
                error_type=type(error).__name__,
                query_index=self._recorder.current_query_index,
            )
            raise
        self._recorder.add(
            component="neo4j",
            stage="execute",
            source=source,
            outcome="succeeded",
            duration_seconds=round(perf_counter() - started, 6),
            columns=list(result.columns),
            row_count=len(result.rows),
            truncated=result.truncated,
            query_index=self._recorder.current_query_index,
        )
        return result


class RecoveryEventHandler(logging.Handler):
    """Capture only the structured, redacted recovery event dictionary."""

    def __init__(self, recorder: EvaluationRecorder) -> None:
        super().__init__()
        self._recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        primary_agent_event = getattr(record, "primary_agent_event", None)
        if isinstance(primary_agent_event, dict):
            self._recorder.add(**dict(primary_agent_event))
            return
        review_event = getattr(record, "decomposition_review_event", None)
        if isinstance(review_event, dict):
            self._recorder.add(**dict(review_event))
            return
        summary_event = getattr(record, "result_summary_event", None)
        if isinstance(summary_event, dict):
            self._recorder.add(**dict(summary_event))
            return
        event = getattr(record, "recovery_event", None)
        if isinstance(event, dict):
            self._recorder.recovery_events.append(dict(event))
            return
        message = record.getMessage()
        if record.name.endswith("question_decomposer") and "回退原问题" in message:
            self._recorder.add(
                component="decomposer",
                stage="fallback",
                outcome="degraded",
                reason=message.rsplit("：", maxsplit=1)[-1],
            )
        elif record.name.endswith("few_shot_router") and "回退 Zero-shot" in message:
            self._recorder.add(
                component="router",
                stage="fallback",
                outcome="degraded",
                reason=message.rsplit("：", maxsplit=1)[-1],
            )


class EvaluationLogObserver:
    """在评测范围内显式订阅脱敏的结构化业务事件。"""

    _LOGGER_NAMES = (
        "text2cypher.application.primary_agent",
        "text2cypher.application.question_decomposer",
        "text2cypher.application.result_summarizer",
        "text2cypher.application.few_shot_router",
        "text2cypher.query_engine.engine",
    )

    def __init__(self, recorder: EvaluationRecorder) -> None:
        self._handler = RecoveryEventHandler(recorder)
        self._loggers = tuple(
            logging.getLogger(name) for name in self._LOGGER_NAMES
        )
        self._previous_levels: tuple[int, ...] = ()

    def __enter__(self) -> EvaluationLogObserver:
        self._previous_levels = tuple(logger.level for logger in self._loggers)
        for logger in self._loggers:
            logger.setLevel(logging.INFO)
            logger.addHandler(self._handler)
        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        for logger, previous_level in zip(
            self._loggers,
            self._previous_levels,
            strict=True,
        ):
            logger.removeHandler(self._handler)
            logger.setLevel(previous_level)
