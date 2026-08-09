from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from threading import Barrier, Lock, get_ident
from typing import Any

import pytest

from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.domain.errors import CypherValidationError, Neo4jAccessError
from text2cypher.domain.models import (
    ChatPrompt,
    DependencyInput,
    DependencyParameter,
    GraphSchema,
    LLMResponse,
    QueryResult,
    QuestionDecomposition,
    SubQueryResponse,
    SubQuestionPlan,
    ValidationReport,
)


class StubSchemaFetcher:
    def fetch(self) -> GraphSchema:
        return GraphSchema()


class StaticDecomposer:
    def __init__(self, decomposition: QuestionDecomposition) -> None:
        self._decomposition = decomposition

    def decompose(
        self,
        question: str,
        schema: GraphSchema,
    ) -> QuestionDecomposition:
        assert question == self._decomposition.original_question
        assert schema == GraphSchema()
        return self._decomposition


class RecordingPromptBuilder:
    def __init__(self) -> None:
        self.calls: list[
            tuple[str, tuple[DependencyParameter, ...], tuple[str, ...]]
        ] = []

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[object, ...] = (),
        *,
        dependency_parameters: tuple[DependencyParameter, ...] = (),
        required_output_columns: tuple[str, ...] = (),
        original_question: str | None = None,
    ) -> ChatPrompt:
        assert schema == GraphSchema()
        assert examples == ()
        self.calls.append((question, dependency_parameters, required_output_columns))
        if original_question is not None:
            assert original_question
        return ChatPrompt(system="system", user=question)


class EchoLLM:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        return LLMResponse(content=prompt.user)


class MappingParser:
    def __init__(self, mapping: Mapping[str, str]) -> None:
        self._mapping = mapping

    def parse(self, text: str) -> str:
        return self._mapping[text]


class RecordingValidator:
    def __init__(self, failure: str | None = None) -> None:
        self._failure = failure
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def validate(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ValidationReport:
        self.calls.append((cypher, parameters))
        if cypher == self._failure:
            raise CypherValidationError("rejected")
        return ValidationReport("r")


class CallbackExecutor:
    def __init__(
        self,
        callback: Callable[[str, Mapping[str, Any] | None], QueryResult],
    ) -> None:
        self._callback = callback
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult:
        self.calls.append((cypher, parameters))
        return self._callback(cypher, parameters)


class StubFormatter:
    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> str:
        return f"{question}:{len(sub_queries)}"


class RecordingCorrector:
    def __init__(self, content: str) -> None:
        self._content = content
        self.kinds: list[str] = []

    def correct(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure_kind: object,
    ) -> LLMResponse:
        del base_prompt, failed_candidate
        self.kinds.append(str(failure_kind))
        return LLMResponse(content=self._content)


def _pipeline(
    decomposition: QuestionDecomposition,
    parser: MappingParser,
    validator: RecordingValidator,
    executor: CallbackExecutor,
    prompt_builder: RecordingPromptBuilder | None = None,
    corrector: RecordingCorrector | None = None,
    recover_empty_results: bool = False,
    max_subquery_workers: int = 3,
) -> Text2CypherPipeline:
    return Text2CypherPipeline(
        schema_fetcher=StubSchemaFetcher(),
        prompt_builder=prompt_builder or RecordingPromptBuilder(),
        llm_client=EchoLLM(),
        cypher_parser=parser,
        cypher_validator=validator,
        cypher_executor=executor,
        result_formatter=StubFormatter(),
        question_decomposer=StaticDecomposer(decomposition),
        cypher_corrector=corrector,
        recover_empty_results=recover_empty_results,
        max_subquery_workers=max_subquery_workers,
    )


def test_pipeline_runs_roots_in_parallel_then_binds_dependency_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    decomposition = QuestionDecomposition(
        "综合查询",
        (
            SubQuestionPlan("q1", "source"),
            SubQuestionPlan("q2", "independent"),
            SubQuestionPlan(
                "q3",
                "dependent",
                (
                    DependencyInput("q1", ("entity_id",)),
                    DependencyInput("q2", ("other_id",)),
                ),
            ),
        ),
    )
    barrier = Barrier(2)
    lock = Lock()
    root_threads: set[int] = set()
    root_finished = 0

    def execute(cypher: str, parameters: Mapping[str, Any] | None) -> QueryResult:
        nonlocal root_finished
        if cypher in {"source-cypher", "independent-cypher"}:
            with lock:
                root_threads.add(get_ident())
            barrier.wait(timeout=1)
            with lock:
                root_finished += 1
            if cypher == "source-cypher":
                return QueryResult(("entity_id",), ({"entity_id": "id-1"},))
            return QueryResult(("other_id",), ({"other_id": "id-2"},))
        assert (
            cypher
            == "UNWIND $dep_q1_rows AS dep_q1 "
            "UNWIND $dep_q2_rows AS dep_q2 "
            "RETURN dep_q1.entity_id + dep_q2.other_id AS result"
        )
        assert root_finished == 2
        assert parameters == {
            "dep_q1_rows": [{"entity_id": "id-1"}],
            "dep_q2_rows": [{"other_id": "id-2"}],
        }
        return QueryResult(("result",), ({"result": "done"},))

    prompt_builder = RecordingPromptBuilder()
    caplog.set_level(logging.INFO, logger="text2cypher.application.pipeline")
    response = _pipeline(
        decomposition,
        MappingParser(
            {
                "source": "source-cypher",
                "independent": "independent-cypher",
                "dependent": (
                    "UNWIND $dep_q1_rows AS dep_q1 "
                    "UNWIND $dep_q2_rows AS dep_q2 "
                    "RETURN dep_q1.entity_id + dep_q2.other_id AS result"
                ),
            }
        ),
        RecordingValidator(),
        CallbackExecutor(execute),
        prompt_builder,
    ).run("综合查询")

    assert len(root_threads) == 2
    assert [item.id for item in response.sub_queries] == ["q1", "q2", "q3"]
    assert response.sub_queries[2].depends_on == ("q1", "q2")
    assert response.sub_queries[2].parameter_sources == {
        "dep_q1_rows": DependencyParameter(
            "dep_q1_rows",
            "q1",
            ("entity_id",),
        ),
        "dep_q2_rows": DependencyParameter(
            "dep_q2_rows",
            "q2",
            ("other_id",),
        ),
    }
    prompt_calls = {
        question: (parameters, outputs)
        for question, parameters, outputs in prompt_builder.calls
    }
    assert prompt_calls["source"][1] == ("entity_id",)
    assert prompt_calls["independent"][1] == ("other_id",)
    assert prompt_calls["dependent"][0] == (
        DependencyParameter("dep_q1_rows", "q1", ("entity_id",)),
        DependencyParameter("dep_q2_rows", "q2", ("other_id",)),
    )
    events = [
        record.subquery_event
        for record in caplog.records
        if hasattr(record, "subquery_event")
    ]
    assert {event["event"] for event in events} == {
        "subquery_scheduled",
        "subquery_started",
        "subquery_succeeded",
    }
    assert {event["subquery_id"] for event in events} == {"q1", "q2", "q3"}
    assert all(event["component"] == "pipeline" for event in events)
    assert "综合查询" not in caplog.text
    assert "id-1" not in caplog.text


def test_output_contract_is_corrected_once_before_dependency_binding() -> None:
    decomposition = QuestionDecomposition(
        "查询依赖",
        (
            SubQuestionPlan("q1", "source"),
            SubQuestionPlan(
                "q2",
                "dependent",
                (DependencyInput("q1", ("entity_id",)),),
            ),
        ),
    )
    corrector = RecordingCorrector("corrected-source")

    def execute(cypher: str, parameters: Mapping[str, Any] | None) -> QueryResult:
        if cypher == "source-cypher":
            return QueryResult(("wrong",), ({"wrong": "id-1"},))
        if cypher == "corrected-source-cypher":
            return QueryResult(("entity_id",), ({"entity_id": "id-1"},))
        assert parameters == {"dep_q1_rows": [{"entity_id": "id-1"}]}
        return QueryResult(("result",), ({"result": "done"},))

    response = _pipeline(
        decomposition,
        MappingParser(
            {
                "source": "source-cypher",
                "corrected-source": "corrected-source-cypher",
                "dependent": (
                    "UNWIND $dep_q1_rows AS input "
                    "RETURN input.entity_id AS result"
                ),
            }
        ),
        RecordingValidator(),
        CallbackExecutor(execute),
        corrector=corrector,
    ).run("查询依赖")

    assert corrector.kinds == ["output_contract"]
    assert response.sub_queries[0].cypher == "corrected-source-cypher"
    assert response.sub_queries[2 - 1].status.value == "success"


def test_dependency_parameter_contract_uses_specialized_single_correction() -> None:
    decomposition = QuestionDecomposition(
        "参数纠错",
        (
            SubQuestionPlan("q1", "source"),
            SubQuestionPlan(
                "q2",
                "dependent",
                (DependencyInput("q1", ("entity_id",)),),
            ),
        ),
    )
    corrector = RecordingCorrector("corrected-dependent")

    def execute(cypher: str, parameters: Mapping[str, Any] | None) -> QueryResult:
        if cypher == "source-cypher":
            return QueryResult(("entity_id",), ({"entity_id": "id-1"},))
        assert cypher == (
            "UNWIND $dep_q1_rows AS row_q1 "
            "RETURN row_q1.entity_id AS result"
        )
        assert parameters == {"dep_q1_rows": [{"entity_id": "id-1"}]}
        return QueryResult(("result",), ({"result": "done"},))

    response = _pipeline(
        decomposition,
        MappingParser(
            {
                "source": "source-cypher",
                "dependent": (
                    "RETURN [row IN $dep_q1_rows | row.entity_id] AS result"
                ),
                "corrected-dependent": (
                    "UNWIND $dep_q1_rows AS row_q1 "
                    "RETURN row_q1.entity_id AS result"
                ),
            }
        ),
        RecordingValidator(),
        CallbackExecutor(execute),
        corrector=corrector,
    ).run("参数纠错")

    assert corrector.kinds == ["dependency_parameter"]
    assert response.sub_queries[1].status.value == "success"


def test_pipeline_returns_partial_and_blocks_only_the_failed_dependency_chain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    decomposition = QuestionDecomposition(
        "部分结果",
        (
            SubQuestionPlan("q1", "failing-source"),
            SubQuestionPlan(
                "q2",
                "blocked-dependent",
                (DependencyInput("q1", ("entity_id",)),),
            ),
            SubQuestionPlan("q3", "independent"),
        ),
    )
    prompt_builder = RecordingPromptBuilder()
    caplog.set_level(logging.INFO, logger="text2cypher.application.pipeline")

    def execute(cypher: str, parameters: Mapping[str, Any] | None) -> QueryResult:
        del parameters
        assert cypher == "independent-cypher"
        return QueryResult(("value",), ({"value": "kept"},))

    response = _pipeline(
        decomposition,
        MappingParser(
            {
                "failing-source": "failing-cypher",
                "blocked-dependent": "dependent-cypher",
                "independent": "independent-cypher",
            }
        ),
        RecordingValidator(failure="failing-cypher"),
        CallbackExecutor(execute),
        prompt_builder,
    ).run("部分结果")

    assert response.status.value == "partial"
    assert [item.status.value for item in response.sub_queries] == [
        "failed",
        "blocked",
        "success",
    ]
    assert response.sub_queries[1].error is not None
    assert response.sub_queries[1].error.kind == "dependency_failed"
    assert "blocked-dependent" not in [call[0] for call in prompt_builder.calls]
    events = [
        record.subquery_event
        for record in caplog.records
        if hasattr(record, "subquery_event")
    ]
    assert {event["event"] for event in events} >= {
        "subquery_scheduled",
        "subquery_started",
        "subquery_succeeded",
        "subquery_failed",
        "subquery_blocked",
    }
    assert "部分结果" not in caplog.text


def test_truncated_parent_only_fails_its_dependency_chain() -> None:
    decomposition = QuestionDecomposition(
        "截断依赖",
        (
            SubQuestionPlan("q1", "source"),
            SubQuestionPlan(
                "q2",
                "dependent",
                (DependencyInput("q1", ("entity_id",)),),
            ),
            SubQuestionPlan("q3", "independent"),
        ),
    )

    def execute(cypher: str, parameters: Mapping[str, Any] | None) -> QueryResult:
        del parameters
        if cypher == "source-cypher":
            return QueryResult(
                ("entity_id",),
                ({"entity_id": "first"},),
                truncated=True,
            )
        assert cypher == "independent-cypher"
        return QueryResult(("value",), ({"value": "kept"},))

    response = _pipeline(
        decomposition,
        MappingParser(
            {
                "source": "source-cypher",
                "dependent": "dependent-cypher",
                "independent": "independent-cypher",
            }
        ),
        RecordingValidator(),
        CallbackExecutor(execute),
    ).run("截断依赖")

    assert response.status.value == "partial"
    assert response.sub_queries[1].status.value == "failed"
    assert response.sub_queries[1].error is not None
    assert response.sub_queries[1].error.kind == "dependency_source_truncated"


def test_empty_dependency_parameter_suppresses_only_child_empty_recovery() -> None:
    decomposition = QuestionDecomposition(
        "空依赖",
        (
            SubQuestionPlan("q1", "source"),
            SubQuestionPlan(
                "q2",
                "dependent",
                (DependencyInput("q1", ("entity_id",)),),
            ),
        ),
    )
    corrector = RecordingCorrector("source")

    def execute(cypher: str, parameters: Mapping[str, Any] | None) -> QueryResult:
        if cypher == "source-cypher":
            return QueryResult(("entity_id",), ())
        assert parameters == {"dep_q1_rows": []}
        return QueryResult(("result",), ())

    response = _pipeline(
        decomposition,
        MappingParser(
            {
                "source": "source-cypher",
                "dependent": (
                    "UNWIND $dep_q1_rows AS input "
                    "RETURN input.entity_id AS result"
                ),
            }
        ),
        RecordingValidator(),
        CallbackExecutor(execute),
        corrector=corrector,
        recover_empty_results=True,
    ).run("空依赖")

    assert [item.status.value for item in response.sub_queries] == [
        "success",
        "success",
    ]
    assert corrector.kinds == ["empty_result"]


def test_neo4j_access_error_remains_global_failure() -> None:
    decomposition = QuestionDecomposition.original("查询受限数据")

    class AccessValidator(RecordingValidator):
        def validate(
            self,
            cypher: str,
            parameters: Mapping[str, Any] | None = None,
        ) -> ValidationReport:
            del cypher, parameters
            raise Neo4jAccessError("denied")

    with pytest.raises(Neo4jAccessError, match="denied"):
        _pipeline(
            decomposition,
            MappingParser({"查询受限数据": "RETURN 1"}),
            AccessValidator(),
            CallbackExecutor(lambda cypher, parameters: QueryResult((), ())),
        ).run("查询受限数据")
