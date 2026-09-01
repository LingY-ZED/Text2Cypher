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
    PrimaryAgentPlan,
    QueryResult,
    QuestionDecomposition,
    ResultSummary,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
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
        return QuestionDecomposition(question, (question,))


class FakePrimaryAgent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def plan(self, question: str) -> PrimaryAgentPlan:
        assert question == "列出服务"
        self.calls.append("primary_agent")
        return PrimaryAgentPlan.fallback(question)


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
        summary: ResultSummary | None = None,
    ) -> str:
        assert question == "列出服务"
        assert sub_queries[0].cypher == "MATCH (n) RETURN n"
        assert sub_queries[0].result.rows[0]["name"] == "demo"
        self.calls.append("formatter")
        return "formatted"


class FakeResultSummarizer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def summarize(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> ResultSummary:
        assert question == "列出服务"
        assert sub_queries[0].result.rows == ({"name": "demo"},)
        self.calls.append("summarizer")
        return ResultSummary(
            "查询到 demo。",
            ResultSummaryMode.TEMPLATE,
            ResultSummaryFallbackReason.DISABLED,
        )


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


def test_pipeline_uses_primary_agent_without_passing_schema() -> None:
    calls: list[str] = []
    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=FakePromptBuilder(calls),
        llm_client=FakeLLMClient(calls),
        cypher_parser=FakeParser(calls),
        cypher_validator=FakeValidator(calls),
        cypher_executor=FakeExecutor(calls),
        result_formatter=FakeFormatter(calls),
        primary_agent=FakePrimaryAgent(calls),
        few_shot_router=FakeFewShotRouter(calls),
    )

    response = pipeline.run("列出服务")

    assert response.sub_queries[0].question == "列出服务"
    assert calls == [
        "schema",
        "primary_agent",
        "router",
        "prompt",
        "llm",
        "parser",
        "validator",
        "executor",
        "formatter",
    ]


def test_pipeline_rejects_primary_agent_and_legacy_decomposer_together() -> None:
    calls: list[str] = []

    with pytest.raises(ValueError, match="不能同时注入"):
        Text2CypherPipeline(
            schema_fetcher=FakeSchemaFetcher(calls),
            prompt_builder=FakePromptBuilder(calls),
            llm_client=FakeLLMClient(calls),
            cypher_parser=FakeParser(calls),
            cypher_validator=FakeValidator(calls),
            cypher_executor=FakeExecutor(calls),
            result_formatter=FakeFormatter(calls),
            primary_agent=FakePrimaryAgent(calls),
            question_decomposer=FakeQuestionDecomposer(calls),
        )


def test_pipeline_summarizes_after_all_sub_queries_and_before_formatting() -> None:
    calls: list[str] = []
    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=FakePromptBuilder(calls),
        llm_client=FakeLLMClient(calls),
        cypher_parser=FakeParser(calls),
        cypher_validator=FakeValidator(calls),
        cypher_executor=FakeExecutor(calls),
        result_formatter=FakeFormatter(calls),
        result_summarizer=FakeResultSummarizer(calls),
    )

    response = pipeline.run("列出服务")

    assert response.summary is not None
    assert response.summary.answer == "查询到 demo。"
    assert calls[-2:] == ["summarizer", "formatter"]


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


def test_pipeline_executes_each_sub_question_in_order_with_one_schema() -> None:
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
            return QuestionDecomposition(
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
        ) -> ChatPrompt:
            assert schema == GraphSchema()
            assert examples == ()
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
    assert calls == [
        "schema",
        "decomposer",
        "router:查询上游",
        "prompt:查询上游",
        "llm:查询上游",
        "parser:查询上游",
        "validator:RETURN '查询上游' AS branch",
        "executor:RETURN '查询上游' AS branch",
        "router:查询下游",
        "prompt:查询下游",
        "llm:查询下游",
        "parser:查询下游",
        "validator:RETURN '查询下游' AS branch",
        "executor:RETURN '查询下游' AS branch",
        "router:查询消息",
        "prompt:查询消息",
        "llm:查询消息",
        "parser:查询消息",
        "validator:RETURN '查询消息' AS branch",
        "executor:RETURN '查询消息' AS branch",
        "formatter",
    ]


def test_pipeline_summarizes_three_sub_queries_once_after_last_execution() -> None:
    calls: list[str] = []

    class ThreeWayDecomposer:
        def decompose(
            self,
            question: str,
            schema: GraphSchema,
        ) -> QuestionDecomposition:
            return QuestionDecomposition(question, ("一", "二", "三"))

    class AnyPromptBuilder:
        def build(
            self,
            schema: GraphSchema,
            question: str,
            examples: tuple[FewShotExample, ...] = (),
        ) -> ChatPrompt:
            del schema, examples
            return ChatPrompt(system="system", user=question)

    class AnyLLM:
        def generate(self, prompt: ChatPrompt) -> LLMResponse:
            return LLMResponse(content=prompt.user)

    class AnyParser:
        def parse(self, text: str) -> str:
            return text

    class AnyValidator:
        def validate(self, cypher: str) -> ValidationReport:
            del cypher
            return ValidationReport(query_type="r")

    class RecordingExecutor:
        def execute(self, cypher: str) -> QueryResult:
            calls.append(f"executor:{cypher}")
            return QueryResult(("结果",), ({"结果": cypher},))

    class RecordingSummarizer:
        def summarize(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> ResultSummary:
            assert question == "复杂问题"
            assert tuple(item.question for item in sub_queries) == ("一", "二", "三")
            calls.append("summarizer")
            return ResultSummary(
                "已完成。",
                ResultSummaryMode.TEMPLATE,
                ResultSummaryFallbackReason.DISABLED,
            )

    class RecordingFormatter:
        def format(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
            summary: ResultSummary | None = None,
        ) -> str:
            del question, sub_queries
            assert summary is not None
            calls.append("formatter")
            return "formatted"

    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=AnyPromptBuilder(),
        llm_client=AnyLLM(),
        cypher_parser=AnyParser(),
        cypher_validator=AnyValidator(),
        cypher_executor=RecordingExecutor(),
        result_formatter=RecordingFormatter(),
        result_summarizer=RecordingSummarizer(),
        question_decomposer=ThreeWayDecomposer(),
    )

    pipeline.run("复杂问题")

    assert calls == [
        "schema",
        "executor:一",
        "executor:二",
        "executor:三",
        "summarizer",
        "formatter",
    ]


def test_pipeline_fails_fast_and_does_not_run_later_sub_questions() -> None:
    calls: list[str] = []

    class FailingIfCalledSummarizer:
        def summarize(
            self,
            question: str,
            sub_queries: tuple[SubQueryResponse, ...],
        ) -> ResultSummary:
            del question, sub_queries
            calls.append("summarizer")
            raise AssertionError("子查询失败后不应总结")

    class ThreeWayDecomposer:
        def decompose(
            self,
            question: str,
            schema: GraphSchema,
        ) -> QuestionDecomposition:
            return QuestionDecomposition(question, ("一", "二", "三"))

    class RecordingPromptBuilder:
        def build(
            self,
            schema: GraphSchema,
            question: str,
            examples: tuple[FewShotExample, ...] = (),
        ) -> ChatPrompt:
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

    pipeline = Text2CypherPipeline(
        schema_fetcher=FakeSchemaFetcher(calls),
        prompt_builder=RecordingPromptBuilder(),
        llm_client=RecordingLLM(),
        cypher_parser=RecordingParser(),
        cypher_validator=RejectSecondValidator(),
        cypher_executor=RecordingExecutor(),
        result_formatter=FakeFormatter(calls),
        result_summarizer=FailingIfCalledSummarizer(),
        question_decomposer=ThreeWayDecomposer(),
    )

    with pytest.raises(CypherValidationError, match="第二个"):
        pipeline.run("复杂问题")

    assert "prompt:三" not in calls
    assert "executor:RETURN '二'" not in calls
    assert "summarizer" not in calls
    assert "formatter" not in calls
