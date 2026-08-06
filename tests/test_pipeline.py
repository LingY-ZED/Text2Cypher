from __future__ import annotations

import pytest

from text2cypher.application.few_shot_router import LLMFewShotRouter
from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.domain.errors import (
    CypherValidationError,
    LLMGenerationError,
    QuestionValidationError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    FewShotExample,
    FewShotSchemaRequirements,
    GraphSchema,
    LLMResponse,
    QueryResult,
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

    with pytest.raises(CypherValidationError, match="不安全"):
        pipeline.run("列出服务")

    assert calls == [
        "schema",
        "router",
        "prompt",
        "llm",
        "parser",
        "validator",
    ]
