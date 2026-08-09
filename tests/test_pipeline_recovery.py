from __future__ import annotations

import logging

import pytest

from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.domain.errors import (
    CypherExecutionError,
    CypherParseError,
    CypherValidationError,
    LLMGenerationError,
    Neo4jConnectionError,
    SubQueryExecutionError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureKind,
    GraphSchema,
    LLMResponse,
    QueryResult,
    QuestionDecomposition,
    SubQueryResponse,
    Text2CypherResponse,
    ValidationReport,
)


class StubSchemaFetcher:
    def fetch(self) -> GraphSchema:
        return GraphSchema()


class StubPromptBuilder:
    def __init__(self) -> None:
        self.prompts: list[ChatPrompt] = []

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[object, ...] = (),
    ) -> ChatPrompt:
        assert schema == GraphSchema()
        assert question == "查询"
        assert examples == ()
        prompt = ChatPrompt(system="system", user="user")
        self.prompts.append(prompt)
        return prompt


class StubLLMClient:
    def __init__(self, content: str) -> None:
        self._content = content
        self.prompts: list[ChatPrompt] = []

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(content=self._content)


class RecordingCorrector:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self._response = response
        self.calls: list[tuple[ChatPrompt, str, CypherFailureKind]] = []

    def correct(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure_kind: CypherFailureKind,
    ) -> LLMResponse:
        self.calls.append((base_prompt, failed_candidate, failure_kind))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class MappingParser:
    def __init__(self, failures: set[str] | None = None) -> None:
        self._failures = failures or set()
        self.calls: list[str] = []

    def parse(self, text: str) -> str:
        self.calls.append(text)
        if text in self._failures:
            raise CypherParseError("无法解析")
        return text


class MappingValidator:
    def __init__(self, failures: set[str] | None = None) -> None:
        self._failures = failures or set()
        self.calls: list[str] = []

    def validate(self, cypher: str) -> ValidationReport:
        self.calls.append(cypher)
        if cypher in self._failures:
            raise CypherValidationError("校验失败")
        return ValidationReport(query_type="r")


class MappingExecutor:
    def __init__(
        self,
        results: dict[str, QueryResult],
        failures: set[str] | None = None,
    ) -> None:
        self._results = results
        self._failures = failures or set()
        self.calls: list[str] = []

    def execute(self, cypher: str) -> QueryResult:
        self.calls.append(cypher)
        if cypher in self._failures:
            raise CypherExecutionError("执行失败")
        return self._results[cypher]


class StubFormatter:
    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> str:
        assert question == "查询"
        assert len(sub_queries) == 1
        return "formatted"


def _pipeline(
    *,
    parser: MappingParser,
    validator: MappingValidator,
    executor: MappingExecutor,
    corrector: RecordingCorrector | None = None,
    recover_empty_results: bool = False,
    initial_content: str = "initial",
) -> Text2CypherPipeline:
    return Text2CypherPipeline(
        schema_fetcher=StubSchemaFetcher(),
        prompt_builder=StubPromptBuilder(),
        llm_client=StubLLMClient(initial_content),
        cypher_parser=parser,
        cypher_validator=validator,
        cypher_executor=executor,
        result_formatter=StubFormatter(),
        cypher_corrector=corrector,
        recover_empty_results=recover_empty_results,
    )


@pytest.mark.parametrize(
    (
        "initial_content",
        "parser_failures",
        "validator_failures",
        "executor_failures",
        "kind",
    ),
    [
        ("unparseable", {"unparseable"}, set(), set(), CypherFailureKind.PARSE),
        ("invalid", set(), {"invalid"}, set(), CypherFailureKind.VALIDATION),
        ("broken", set(), set(), {"broken"}, CypherFailureKind.EXECUTION),
    ],
)
def test_pipeline_corrects_each_repairable_failure_once(
    initial_content: str,
    parser_failures: set[str],
    validator_failures: set[str],
    executor_failures: set[str],
    kind: CypherFailureKind,
) -> None:
    corrected_result = QueryResult(("value",), ({"value": 1},))
    corrector = RecordingCorrector(LLMResponse(content="corrected"))
    parser = MappingParser(parser_failures)
    validator = MappingValidator(validator_failures)
    executor = MappingExecutor(
        {"corrected": corrected_result},
        executor_failures,
    )

    response = _pipeline(
        parser=parser,
        validator=validator,
        executor=executor,
        corrector=corrector,
        initial_content=initial_content,
    ).run("查询")

    assert response.sub_queries[0].cypher == "corrected"
    assert response.sub_queries[0].result == corrected_result
    assert corrector.calls == [
        (ChatPrompt(system="system", user="user"), initial_content, kind)
    ]
    assert parser.calls == [initial_content, "corrected"]
    assert validator.calls[-1] == "corrected"
    assert executor.calls[-1] == "corrected"


def test_pipeline_does_not_correct_connection_error() -> None:
    corrector = RecordingCorrector(LLMResponse(content="corrected"))

    class ConnectionValidator(MappingValidator):
        def validate(self, cypher: str) -> ValidationReport:
            del cypher
            raise Neo4jConnectionError("暂时不可用")

    with pytest.raises(SubQueryExecutionError, match="均未成功"):
        _pipeline(
            parser=MappingParser(),
            validator=ConnectionValidator(),
            executor=MappingExecutor({}),
            corrector=corrector,
        ).run("查询")

    assert corrector.calls == []


def test_pipeline_stops_after_one_unsuccessful_correction() -> None:
    corrector = RecordingCorrector(LLMResponse(content="still-unparseable"))

    with pytest.raises(SubQueryExecutionError, match="均未成功"):
        _pipeline(
            parser=MappingParser({"initial", "still-unparseable"}),
            validator=MappingValidator(),
            executor=MappingExecutor({}),
            corrector=corrector,
        ).run("查询")

    assert len(corrector.calls) == 1


def test_pipeline_replaces_empty_result_only_when_correction_is_non_empty() -> None:
    original_result = QueryResult(("value",), ())
    corrected_result = QueryResult(("value",), ({"value": 2},))
    corrector = RecordingCorrector(LLMResponse(content="corrected"))

    response = _pipeline(
        parser=MappingParser(),
        validator=MappingValidator(),
        executor=MappingExecutor(
            {"initial": original_result, "corrected": corrected_result}
        ),
        corrector=corrector,
        recover_empty_results=True,
    ).run("查询")

    assert response == Text2CypherResponse(
        question="查询",
        sub_queries=(
            SubQueryResponse(
                question="查询",
                cypher="corrected",
                result=corrected_result,
            ),
        ),
        formatted="formatted",
    )
    assert corrector.calls[0][2] is CypherFailureKind.EMPTY_RESULT


@pytest.mark.parametrize(
    "correction",
    [
        LLMResponse(content="still-empty"),
        LLMGenerationError("模型不可用"),
    ],
)
def test_pipeline_keeps_original_empty_result_when_recovery_has_no_improvement(
    correction: LLMResponse | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_result = QueryResult(("value",), ())
    corrector = RecordingCorrector(correction)
    caplog.set_level(logging.WARNING)

    response = _pipeline(
        parser=MappingParser(),
        validator=MappingValidator(),
        executor=MappingExecutor(
            {"initial": original_result, "still-empty": original_result}
        ),
        corrector=corrector,
        recover_empty_results=True,
    ).run("查询")

    assert response.sub_queries[0].cypher == "initial"
    assert response.sub_queries[0].result == original_result
    assert len(corrector.calls) == 1
    events = [
        record.recovery_event
        for record in caplog.records
        if hasattr(record, "recovery_event")
    ]
    assert events[0]["event"] == "cypher_correction_started"
    assert events[-1]["event"] == "empty_result_original_kept"
    assert all(event["component"] == "pipeline" for event in events)
    assert all(event["stage"] == "cypher_correction" for event in events)
    assert "initial" not in caplog.text


def test_pipeline_stops_later_sub_queries_after_correction_is_exhausted() -> None:
    calls: list[str] = []
    corrector = RecordingCorrector(LLMResponse(content="still-unparseable"))

    class TwoWayDecomposer:
        def decompose(
            self,
            question: str,
            schema: GraphSchema,
        ) -> QuestionDecomposition:
            assert question == "复杂查询"
            assert schema == GraphSchema()
            calls.append("decomposer")
            return QuestionDecomposition.independent(
                question,
                ("第一分支", "第二分支"),
            )

    class AnyPromptBuilder:
        def build(
            self,
            schema: GraphSchema,
            question: str,
            examples: tuple[object, ...] = (),
            *,
            original_question: str | None = None,
        ) -> ChatPrompt:
            assert schema == GraphSchema()
            assert examples == ()
            assert original_question == "复杂查询"
            calls.append(f"prompt:{question}")
            return ChatPrompt(system="system", user=question)

    class BranchLLM:
        def generate(self, prompt: ChatPrompt) -> LLMResponse:
            calls.append(f"llm:{prompt.user}")
            return LLMResponse(content="unparseable")

    class FailingFormatter:
        def format(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> str:
            del question, sub_queries
            pytest.fail("全部子查询失败后不得格式化结果")

    pipeline = Text2CypherPipeline(
        schema_fetcher=StubSchemaFetcher(),
        prompt_builder=AnyPromptBuilder(),
        llm_client=BranchLLM(),
        cypher_parser=MappingParser({"unparseable", "still-unparseable"}),
        cypher_validator=MappingValidator(),
        cypher_executor=MappingExecutor({}),
        result_formatter=FailingFormatter(),
        question_decomposer=TwoWayDecomposer(),
        cypher_corrector=corrector,
    )

    with pytest.raises(SubQueryExecutionError, match="均未成功"):
        pipeline.run("复杂查询")

    assert calls[0] == "decomposer"
    assert {"prompt:第一分支", "prompt:第二分支"} <= set(calls)
    assert {"llm:第一分支", "llm:第二分支"} <= set(calls)
    assert len(corrector.calls) == 2


def test_pipeline_can_disable_empty_result_recovery_independently() -> None:
    original_result = QueryResult(("value",), ())
    corrector = RecordingCorrector(LLMResponse(content="corrected"))

    response = _pipeline(
        parser=MappingParser(),
        validator=MappingValidator(),
        executor=MappingExecutor({"initial": original_result}),
        corrector=corrector,
        recover_empty_results=False,
    ).run("查询")

    assert response.sub_queries[0].cypher == "initial"
    assert corrector.calls == []
