"""应用层的确定性 Text2Cypher 用例编排。"""

from __future__ import annotations

from collections.abc import Callable

from text2cypher.domain.errors import QuestionValidationError
from text2cypher.domain.models import Text2CypherResponse
from text2cypher.domain.ports import (
    CypherExecutor,
    CypherParser,
    CypherValidator,
    LLMClient,
    PromptBuilder,
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
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        self._schema_fetcher = schema_fetcher
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._cypher_parser = cypher_parser
        self._cypher_validator = cypher_validator
        self._cypher_executor = cypher_executor
        self._result_formatter = result_formatter
        self._close_callback = close_callback
        self._closed = False

    def run(self, question: str) -> Text2CypherResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise QuestionValidationError("问题不能为空")

        schema = self._schema_fetcher.fetch()
        prompt = self._prompt_builder.build(schema, normalized_question)
        llm_response = self._llm_client.generate(prompt)
        cypher = self._cypher_parser.parse(llm_response.content)
        self._cypher_validator.validate(cypher)
        result = self._cypher_executor.execute(cypher)
        formatted = self._result_formatter.format(normalized_question, cypher, result)
        return Text2CypherResponse(
            question=normalized_question,
            cypher=cypher,
            result=result,
            formatted=formatted,
        )

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
