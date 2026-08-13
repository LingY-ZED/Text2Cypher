"""Deterministic probes for the four one-shot Cypher recovery paths."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.components.cypher_parser import DefaultCypherParser
from text2cypher.domain.errors import CypherExecutionError, CypherValidationError
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureContext,
    CypherFailureKind,
    FewShotExample,
    GraphSchema,
    LLMResponse,
    QueryResult,
    ResultSummary,
    SubQueryResponse,
    ValidationReport,
)


class _SchemaFetcher:
    def fetch(self) -> GraphSchema:
        return GraphSchema()


class _PromptBuilder:
    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[FewShotExample, ...] = (),
    ) -> ChatPrompt:
        del schema, question, examples
        return ChatPrompt(system="system", user="user")


class _LLM:
    def __init__(self, content: str) -> None:
        self._content = content

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        return LLMResponse(content=self._content)


@dataclass
class _Corrector:
    kinds: list[CypherFailureKind] = field(default_factory=list)

    def correct(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure: CypherFailureContext,
    ) -> LLMResponse:
        del base_prompt, failed_candidate
        self.kinds.append(failure.kind)
        return LLMResponse(content="RETURN 1 AS recovered")


class _Validator:
    def __init__(self, fail_initial: bool = False) -> None:
        self._fail_initial = fail_initial

    def validate(self, cypher: str) -> ValidationReport:
        if self._fail_initial and "initial" in cypher:
            raise CypherValidationError("injected validation failure")
        return ValidationReport(query_type="r")


class _Executor:
    def __init__(
        self,
        *,
        fail_initial: bool = False,
        empty_initial: bool = False,
    ) -> None:
        self._fail_initial = fail_initial
        self._empty_initial = empty_initial

    def execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult:
        del parameters
        if self._fail_initial and "initial" in cypher:
            raise CypherExecutionError("injected execution failure")
        if self._empty_initial and "initial" in cypher:
            return QueryResult(columns=("value",), rows=())
        return QueryResult(columns=("recovered",), rows=({"recovered": 1},))


class _Formatter:
    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
        summary: ResultSummary | None = None,
    ) -> str:
        del question, sub_queries, summary
        return "formatted"


def run_recovery_probes() -> dict[str, bool]:
    """Run parse, validation, execution, and empty-result probes without I/O."""

    logger = logging.getLogger("text2cypher.application.pipeline")
    handler = logging.NullHandler()
    logger.addHandler(handler)
    try:
        return {
            "parse": _run_probe("", CypherFailureKind.PARSE),
            "validation": _run_probe(
                "RETURN 0 AS initial",
                CypherFailureKind.VALIDATION,
                fail_validation=True,
            ),
            "execution": _run_probe(
                "RETURN 0 AS initial",
                CypherFailureKind.EXECUTION,
                fail_execution=True,
            ),
            "empty_result": _run_probe(
                "RETURN 0 AS initial",
                CypherFailureKind.EMPTY_RESULT,
                empty_initial=True,
            ),
        }
    finally:
        logger.removeHandler(handler)


def _run_probe(
    initial: str,
    expected_kind: CypherFailureKind,
    *,
    fail_validation: bool = False,
    fail_execution: bool = False,
    empty_initial: bool = False,
) -> bool:
    corrector = _Corrector()
    pipeline = Text2CypherPipeline(
        schema_fetcher=_SchemaFetcher(),
        prompt_builder=_PromptBuilder(),
        llm_client=_LLM(initial),
        cypher_parser=DefaultCypherParser(),
        cypher_validator=_Validator(fail_validation),
        cypher_executor=_Executor(
            fail_initial=fail_execution,
            empty_initial=empty_initial,
        ),
        result_formatter=_Formatter(),
        cypher_corrector=corrector,
        recover_empty_results=empty_initial,
    )
    response = pipeline.run("probe")
    return bool(
        corrector.kinds == [expected_kind]
        and response.sub_queries[0].cypher == "RETURN 1 AS recovered"
        and response.sub_queries[0].result.rows
    )
