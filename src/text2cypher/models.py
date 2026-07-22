"""Domain models shared by the pipeline and its adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class PropertySchema:
    """A property available on a node label or relationship type."""

    name: str
    types: tuple[str, ...] = ()
    mandatory: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "property name"))
        object.__setattr__(self, "types", tuple(self.types))


@dataclass(frozen=True, slots=True)
class NodeSchema:
    """A node label and its available properties."""

    name: str
    properties: tuple[PropertySchema, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, "node label"))
        object.__setattr__(self, "properties", tuple(self.properties))


@dataclass(frozen=True, slots=True)
class RelationshipSchema:
    """A relationship type and its available properties."""

    name: str
    properties: tuple[PropertySchema, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _require_text(self.name, "relationship type")
        )
        object.__setattr__(self, "properties", tuple(self.properties))


@dataclass(frozen=True, slots=True)
class RelationshipPattern:
    """An observed directed relationship pattern in the graph."""

    start_labels: tuple[str, ...]
    relationship_type: str
    end_labels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_labels", tuple(self.start_labels))
        object.__setattr__(self, "end_labels", tuple(self.end_labels))
        object.__setattr__(
            self,
            "relationship_type",
            _require_text(self.relationship_type, "relationship type"),
        )


@dataclass(frozen=True, slots=True)
class GraphSchema:
    """The structured graph schema injected into a Text2Cypher prompt."""

    nodes: tuple[NodeSchema, ...] = ()
    relationships: tuple[RelationshipSchema, ...] = ()
    patterns: tuple[RelationshipPattern, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "relationships", tuple(self.relationships))
        object.__setattr__(self, "patterns", tuple(self.patterns))


@dataclass(frozen=True, slots=True)
class ChatPrompt:
    """System and user messages sent to a chat-completions LLM."""

    system: str
    user: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "system", _require_text(self.system, "system prompt"))
        object.__setattr__(self, "user", _require_text(self.user, "user prompt"))


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """The subset of an LLM response required by the parser."""

    content: str
    model: str | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Evidence returned by a completed read-only validation step."""

    query_type: str
    notifications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "query_type",
            _require_text(self.query_type, "query type"),
        )
        object.__setattr__(self, "notifications", tuple(self.notifications))


@dataclass(frozen=True, slots=True)
class QueryResult:
    """A bounded, JSON-friendly set of records returned by Neo4j."""

    columns: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(self.rows))


@dataclass(frozen=True, slots=True)
class Text2CypherResponse:
    """The successful public result of one Text2Cypher request."""

    question: str
    cypher: str
    result: QueryResult
    formatted: str
