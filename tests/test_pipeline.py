from __future__ import annotations

import pytest

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.domain.errors import (
    CypherValidationError,
    LLMGenerationError,
    QuestionValidationError,
    SubQueryExecutionError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    LLMResponse,
    QueryResult,
    QuestionDecomposition,
    SubQueryResponse,
    Text2CypherResponse,
    ValidationReport,
)


class FakeSchemaFetcher:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def fetch(self) -> GraphSchema:
        self.calls.append("schema")
        return GraphSchema()


class FakePromptBuilder:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.examples: tuple[FewShotExample, ...] | None = None

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[FewShotExample, ...] = (),
    ) -> ChatPrompt:
        assert schema == GraphSchema()
        assert question == "列出服务"
        self.examples = examples
        self.calls.append("prompt")
        return ChatPrompt(system="system", user="user")


class FakeFewShotRouter:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def route(
        self,
        question: str,
        schema: GraphSchema,
    ) -> tuple[FewShotExample, ...]:
        assert question == "列出服务"
        assert schema == GraphSchema()
        self.calls.append("router")
        return (
            FewShotExample(
                id="service-list",
                category="simple",
                question="列出服务",
                cypher="MATCH (n) RETURN n",
                aliases=("服务列表",),
                tags=("服务",),
                schema_requirements=FewShotSchemaRequirements(),
            ),
        )


class FakeQuestionDecomposer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def decompose(
        self,
        question: str,
        schema: GraphSchema,
    ) -> QuestionDecomposition:
        assert question == "列出服务"
        assert schema == GraphSchema()
        self.calls.append("decomposer")
        return QuestionDecomposition.original(question)


class FakeLLMClient:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        assert prompt.user == "user"
        self.calls.append("llm")
        return LLMResponse(content="raw")


class RouterFailureThenFinalLLMClient:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self._router_attempted = False

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        if not self._router_attempted:
            self._router_attempted = True
            assert "示例路由器" in prompt.system
            self.calls.append("router_llm")
            raise LLMGenerationError("router unavailable")
        assert prompt.user == "user"
        self.calls.append("llm")
        return LLMResponse(content="raw")


class FakeParser:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def parse(self, text: str) -> str:
        assert text == "raw"
        self.calls.append("parser")
        return "MATCH (n) RETURN n"


class FakeValidator:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def validate(self, cypher: str) -> ValidationReport:
        assert cypher == "MATCH (n) RETURN n"
        self.calls.append("validator")
        return ValidationReport(query_type="r")


class FakeExecutor:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def execute(self, cypher: str) -> QueryResult:
        assert cypher == "MATCH (n) RETURN n"
        self.calls.append("executor")
        return QueryResult(columns=("name",), rows=({"name": "demo"},))


class FakeFormatter:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> str:
        assert question == "列出服务"
        assert sub_queries[0].cypher == "MATCH (n) RETURN n"
        assert sub_queries[0].result.rows[0]["name"] == "demo"
        self.calls.append("formatter")
        return "formatted"


def _pipeline(calls: list[str]) -> Text2CypherPipeline:
    return Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=FakePromptBuilder(calls),
        llm_client=FakeLLMClient(calls),
        cypher_parser=FakeParser(calls),
        cypher_validator=FakeValidator(calls),
        cypher_executor=FakeExecutor(calls),
        result_formatter=FakeFormatter(calls),
        question_decomposer=FakeQuestionDecomposer(calls),
        few_shot_router=FakeFewShotRouter(calls),
    )


def test_pipeline_runs_every_stage_in_order() -> None:
    calls: list[str] = []

    response = _pipeline(calls).run("  列出服务  ")

    assert response == Text2CypherResponse(
        question="列出服务",
        sub_queries=(
            SubQueryResponse(
                question="列出服务",
                cypher="MATCH (n) RETURN n",
                result=QueryResult(
                    columns=("name",),
                    rows=({"name": "demo"},),
                ),
            ),
        ),
        formatted="formatted",
    )
    assert calls == [
        "schema",
        "decomposer",
        "router",
        "prompt",
        "llm",
        "parser",
        "validator",
        "executor",
        "formatter",
    ]


def test_pipeline_rejects_blank_question_before_any_stage() -> None:
    calls: list[str] = []

    with pytest.raises(QuestionValidationError):
        _pipeline(calls).run("   ")

    assert calls == []


def test_pipeline_closes_shared_resources_only_once() -> None:
    calls: list[str] = []
    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=FakePromptBuilder(calls),
        llm_client=FakeLLMClient(calls),
        cypher_parser=FakeParser(calls),
        cypher_validator=FakeValidator(calls),
        cypher_executor=FakeExecutor(calls),
        result_formatter=FakeFormatter(calls),
        close_callback=lambda: calls.append("close"),
    )

    pipeline.close()
    pipeline.close()

    assert calls == ["close"]


def test_pipeline_skips_router_when_few_shot_is_disabled() -> None:
    calls: list[str] = []
    prompt_builder = FakePromptBuilder(calls)
    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=prompt_builder,
        llm_client=FakeLLMClient(calls),
        cypher_parser=FakeParser(calls),
        cypher_validator=FakeValidator(calls),
        cypher_executor=FakeExecutor(calls),
        result_formatter=FakeFormatter(calls),
    )

    pipeline.run("列出服务")

    assert prompt_builder.examples == ()
    assert calls == [
        "schema",
        "prompt",
        "llm",
        "parser",
        "validator",
        "executor",
        "formatter",
    ]


def test_pipeline_continues_zero_shot_when_router_model_call_fails() -> None:
    calls: list[str] = []
    prompt_builder = FakePromptBuilder(calls)
    shared_llm_client = RouterFailureThenFinalLLMClient(calls)
    example = FewShotExample(
        id="fallback-example",
        category="test",
        question="示例",
        cypher="MATCH (n) RETURN n",
        aliases=("别名",),
        tags=("测试",),
        schema_requirements=FewShotSchemaRequirements(),
    )
    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=prompt_builder,
        llm_client=shared_llm_client,
        cypher_parser=FakeParser(calls),
        cypher_validator=FakeValidator(calls),
        cypher_executor=FakeExecutor(calls),
        result_formatter=FakeFormatter(calls),
        few_shot_router=LLMFewShotRouter((example,), shared_llm_client),
    )

    pipeline.run("列出服务")

    assert prompt_builder.examples == ()
    assert calls == [
        "schema",
        "router_llm",
        "prompt",
        "llm",
        "parser",
        "validator",
        "executor",
        "formatter",
    ]


def test_pipeline_stops_before_execution_when_validation_fails() -> None:
    calls: list[str] = []

    class RejectingValidator(FakeValidator):
        def validate(self, cypher: str) -> ValidationReport:
            self.calls.append("validator")
            raise CypherValidationError("不安全")

    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=FakePromptBuilder(calls),
        llm_client=FakeLLMClient(calls),
        cypher_parser=FakeParser(calls),
        cypher_validator=RejectingValidator(calls),
        cypher_executor=FakeExecutor(calls),
        result_formatter=FakeFormatter(calls),
        few_shot_router=FakeFewShotRouter(calls),
    )

    with pytest.raises(SubQueryExecutionError, match="均未成功"):
        pipeline.run("列出服务")

    assert calls == [
        "schema",
        "router",
        "prompt",
        "llm",
        "parser",
        "validator",
    ]


def test_pipeline_executes_independent_sub_questions_with_one_schema() -> None:
    calls: list[str] = []

    class ThreeWayDecomposer:
        def decompose(
            self,
            question: str,
            schema: GraphSchema,
        ) -> QuestionDecomposition:
            assert question == "综合分析"
            assert schema == GraphSchema()
            calls.append("decomposer")
            return QuestionDecomposition.independent(
                question,
                ("查询上游", "查询下游", "查询消息"),
            )

    class BranchRouter:
        def route(
            self,
            question: str,
            schema: GraphSchema,
        ) -> tuple[FewShotExample, ...]:
            assert schema == GraphSchema()
            calls.append(f"router:{question}")
            return ()

    class BranchPromptBuilder:
        def build(
            self,
            schema: GraphSchema,
            question: str,
            examples: tuple[FewShotExample, ...] = (),
            *,
            original_question: str | None = None,
        ) -> ChatPrompt:
            assert schema == GraphSchema()
            assert examples == ()
            assert original_question == "综合分析"
            calls.append(f"prompt:{question}")
            return ChatPrompt(system="system", user=question)

    class BranchLLMClient:
        def generate(self, prompt: ChatPrompt) -> LLMResponse:
            calls.append(f"llm:{prompt.user}")
            return LLMResponse(content=prompt.user)

    class BranchParser:
        def parse(self, text: str) -> str:
            calls.append(f"parser:{text}")
            return f"RETURN '{text}' AS branch"

    class BranchValidator:
        def validate(self, cypher: str) -> ValidationReport:
            calls.append(f"validator:{cypher}")
            return ValidationReport(query_type="r")

    class BranchExecutor:
        def execute(self, cypher: str) -> QueryResult:
            calls.append(f"executor:{cypher}")
            return QueryResult(("branch",), ({"branch": cypher},))

    class BranchFormatter:
        def format(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> str:
            assert question == "综合分析"
            assert tuple(item.question for item in sub_queries) == (
                "查询上游",
                "查询下游",
                "查询消息",
            )
            calls.append("formatter")
            return "formatted"

    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=BranchPromptBuilder(),
        llm_client=BranchLLMClient(),
        cypher_parser=BranchParser(),
        cypher_validator=BranchValidator(),
        cypher_executor=BranchExecutor(),
        result_formatter=BranchFormatter(),
        question_decomposer=ThreeWayDecomposer(),
        few_shot_router=BranchRouter(),
    )

    response = pipeline.run("综合分析")

    assert response.decomposed is True
    assert calls.count("schema") == 1
    assert calls[:2] == ["schema", "decomposer"]
    assert calls[-1] == "formatter"
    for question in ("查询上游", "查询下游", "查询消息"):
        assert f"router:{question}" in calls
        assert f"prompt:{question}" in calls
        assert f"llm:{question}" in calls
        assert f"parser:{question}" in calls
        assert f"validator:RETURN '{question}' AS branch" in calls
        assert f"executor:RETURN '{question}' AS branch" in calls


def test_pipeline_returns_partial_when_an_independent_sub_query_fails() -> None:
    calls: list[str] = []

    class ThreeWayDecomposer:
        def decompose(
            self,
            question: str,
            schema: GraphSchema,
        ) -> QuestionDecomposition:
            return QuestionDecomposition.independent(question, ("一", "二", "三"))

    class RecordingPromptBuilder:
        def build(
            self,
            schema: GraphSchema,
            question: str,
            examples: tuple[FewShotExample, ...] = (),
            *,
            original_question: str | None = None,
        ) -> ChatPrompt:
            assert original_question == "复杂问题"
            calls.append(f"prompt:{question}")
            return ChatPrompt(system="system", user=question)

    class RecordingLLM:
        def generate(self, prompt: ChatPrompt) -> LLMResponse:
            return LLMResponse(content=prompt.user)

    class RecordingParser:
        def parse(self, text: str) -> str:
            return f"RETURN '{text}'"

    class RejectSecondValidator:
        def validate(self, cypher: str) -> ValidationReport:
            calls.append(f"validator:{cypher}")
            if "二" in cypher:
                raise CypherValidationError("第二个子查询不安全")
            return ValidationReport(query_type="r")

    class RecordingExecutor:
        def execute(self, cypher: str) -> QueryResult:
            calls.append(f"executor:{cypher}")
            return QueryResult((), ())

    class PartialFormatter:
        def format(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> str:
            assert question == "复杂问题"
            assert len(sub_queries) == 3
            calls.append("formatter")
            return "formatted"

    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=RecordingPromptBuilder(),
        llm_client=RecordingLLM(),
        cypher_parser=RecordingParser(),
        cypher_validator=RejectSecondValidator(),
        cypher_executor=RecordingExecutor(),
        result_formatter=PartialFormatter(),
        question_decomposer=ThreeWayDecomposer(),
    )

    response = pipeline.run("复杂问题")

    assert response.status.value == "partial"
    assert [item.status.value for item in response.sub_queries] == [
        "success",
        "failed",
        "success",
    ]
    assert "prompt:三" in calls
    assert "executor:RETURN '二'" not in calls
