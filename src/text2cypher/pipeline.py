"""The deterministic orchestration path shared by CLI and future APIs."""

from __future__ import annotations

from text2cypher.errors import QuestionValidationError
from text2cypher.models import Text2CypherResponse
from text2cypher.ports import (
    CypherExecutor,
    CypherParser,
    CypherValidator,
    LLMClient,
    PromptBuilder,
    ResultFormatter,
    SchemaFetcher,
)


class Text2CypherPipeline:
    """Run the seven-stage Text2Cypher flow with injected implementations."""

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
    ) -> None:
        self._schema_fetcher = schema_fetcher
        self._prompt_builder = prompt_builder
        self._llm_client = llm_client
        self._cypher_parser = cypher_parser
        self._cypher_validator = cypher_validator
        self._cypher_executor = cypher_executor
        self._result_formatter = result_formatter

    def run(self, question: str) -> Text2CypherResponse:
        normalized_question = question.strip()
        if not normalized_question:
            raise QuestionValidationError("question must not be blank")

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

