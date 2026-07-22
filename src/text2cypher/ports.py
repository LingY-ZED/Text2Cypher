"""Ports that isolate the pipeline from Neo4j and LLM provider details."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from text2cypher.models import (
    ChatPrompt,
    GraphSchema,
    LLMResponse,
    QueryResult,
    ValidationReport,
)


@runtime_checkable
class SchemaFetcher(Protocol):
    """Fetches the live graph schema."""

    def fetch(self) -> GraphSchema: ...


@runtime_checkable
class PromptBuilder(Protocol):
    """Builds a provider-independent chat prompt."""

    def build(self, schema: GraphSchema, question: str) -> ChatPrompt: ...


@runtime_checkable
class LLMClient(Protocol):
    """Generates Cypher through a general LLM API."""

    def generate(self, prompt: ChatPrompt) -> LLMResponse: ...


@runtime_checkable
class CypherParser(Protocol):
    """Extracts one normalized Cypher statement from model output."""

    def parse(self, text: str) -> str: ...


@runtime_checkable
class CypherValidator(Protocol):
    """Validates that Cypher is safe and read-only before execution."""

    def validate(self, cypher: str) -> ValidationReport: ...


@runtime_checkable
class CypherExecutor(Protocol):
    """Executes an already-approved read-only Cypher query."""

    def execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult: ...


@runtime_checkable
class ResultFormatter(Protocol):
    """Formats a query result for a human or CLI consumer."""

    def format(self, question: str, cypher: str, result: QueryResult) -> str: ...
