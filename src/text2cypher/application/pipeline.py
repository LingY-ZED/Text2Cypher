"""应用层的确定性 Text2Cypher 用例编排。"""

from __future__ import annotations

from collections.abc import Callable

from text2cypher.domain.errors import (
    CypherExecutionError,
    CypherParseError,
    CypherValidationError,
    LLMGenerationError,
    Neo4jAccessError,
    Neo4jConnectionError,
    QuestionValidationError,
)
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureKind,
    GraphSchema,
    QueryResult,
    QuestionDecomposition,
    SubQueryResponse,
    Text2CypherResponse,
)
from text2cypher.domain.ports import (
    CypherCorrector,
    CypherExecutor,
    CypherParser,
    CypherValidator,
    FewShotRouter,
    LLMClient,
    PromptBuilder,
    QuestionDecomposer,
    ResultFormatter,
    SchemaFetcher,
)


class Text2CypherPipeline:
    """使用注入实现运行七阶段 Text2Cypher 流程。"""

    def __init__(
        self,
        *,
        schema_fetcher: SchemaFetcher,
        prompt_builder: PromptBuilder,
        llm_client: LLMClient,
        cypher_parser: CypherParser,
        cypher_validator: CypherValidator,
        cypher_executor: CypherExecutor,
        result_formatter: ResultFormatter,
        question_decomposer: QuestionDecomposer | None = None,
        few_shot_router: FewShotRouter | None = None,
        cypher_corrector: CypherCorrector | None = None,
        recover_empty_results: bool = False,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        self._schema_fetcher = schema_fetcher
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._cypher_parser = cypher_parser
        self._cypher_validator = cypher_validator
        self._cypher_executor = cypher_executor
        self._result_formatter = result_formatter
        self._question_decomposer = question_decomposer
        self._few_shot_router = few_shot_router
        self._cypher_corrector = cypher_corrector
        self._recover_empty_results = recover_empty_results
        self._close_callback = close_callback
        self._closed = False

    def run(self, question: str) -> Text2CypherResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise QuestionValidationError("问题不能为空")

        schema = self._schema_fetcher.fetch()
        decomposition = (
            self._question_decomposer.decompose(normalized_question, schema)
            if self._question_decomposer is not None
            else QuestionDecomposition(
                original_question=normalized_question,
                sub_questions=(normalized_question,),
            )
        )
        sub_queries = tuple(
            self._run_sub_query(schema, sub_question)
            for sub_question in decomposition.sub_questions
        )
        formatted = self._result_formatter.format(
            normalized_question,
            sub_queries,
        )
        return Text2CypherResponse(
            question=normalized_question,
            sub_queries=sub_queries,
            formatted=formatted,
        )

    def _run_sub_query(
        self,
        schema: GraphSchema,
        question: str,
    ) -> SubQueryResponse:
        """按固定顺序生成、校验并执行一个独立子问题。"""

        examples = (
            self._few_shot_router.route(question, schema)
            if self._few_shot_router is not None
            else ()
        )
        prompt = self._prompt_builder.build(
            schema,
            question,
            examples,
        )
        llm_response = self._llm_client.generate(prompt)
        try:
            cypher = self._cypher_parser.parse(llm_response.content)
        except CypherParseError:
            if self._cypher_corrector is None:
                raise
            cypher, result = self._correct_and_execute(
                prompt,
                llm_response.content,
                CypherFailureKind.PARSE,
            )
        else:
            try:
                self._cypher_validator.validate(cypher)
                result = self._cypher_executor.execute(cypher)
            except CypherValidationError:
                if self._cypher_corrector is None:
                    raise
                cypher, result = self._correct_and_execute(
                    prompt,
                    cypher,
                    CypherFailureKind.VALIDATION,
                )
            except CypherExecutionError:
                if self._cypher_corrector is None:
                    raise
                cypher, result = self._correct_and_execute(
                    prompt,
                    cypher,
                    CypherFailureKind.EXECUTION,
                )

        if (
            not result.rows
            and self._recover_empty_results
            and self._cypher_corrector is not None
        ):
            cypher, result = self._recover_empty_result(prompt, cypher, result)
        return SubQueryResponse(
            question=question,
            cypher=cypher,
            result=result,
        )

    def _correct_and_execute(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure_kind: CypherFailureKind,
    ) -> tuple[str, QueryResult]:
        """仅执行一次修正，并让修正版重新通过完整的安全链路。"""

        corrector = self._cypher_corrector
        if corrector is None:
            raise AssertionError("纠错调用前必须注入 CypherCorrector")
        corrected = corrector.correct(
            base_prompt,
            failed_candidate,
            failure_kind,
        )
        cypher = self._cypher_parser.parse(corrected.content)
        self._cypher_validator.validate(cypher)
        result = self._cypher_executor.execute(cypher)
        return cypher, result

    def _recover_empty_result(
        self,
        base_prompt: ChatPrompt,
        original_cypher: str,
        original_result: QueryResult,
    ) -> tuple[str, QueryResult]:
        """尽力复核空结果；没有安全的非空改进时保留原结果。"""

        try:
            corrected_cypher, corrected_result = self._correct_and_execute(
                base_prompt,
                original_cypher,
                CypherFailureKind.EMPTY_RESULT,
            )
        except (
            LLMGenerationError,
            CypherParseError,
            CypherValidationError,
            CypherExecutionError,
            Neo4jAccessError,
            Neo4jConnectionError,
        ):
            return original_cypher, original_result
        if corrected_result.rows:
            return corrected_cypher, corrected_result
        return original_cypher, original_result

    def close(self) -> None:
        """关闭流水线持有的外部资源，重复调用安全。"""

        if self._closed:
            return
        self._closed = True
        if self._close_callback is not None:
            self._close_callback()

    def __enter__(self) -> Text2CypherPipeline:
        """进入上下文时返回当前流水线。"""

        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        """离开上下文时关闭外部资源。"""

        del exception_type, exception, traceback
        self.close()
