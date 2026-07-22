"""Stage-specific errors exposed by the Text2Cypher pipeline."""


class Text2CypherError(RuntimeError):
    """Base error for expected Text2Cypher failures."""


class ConfigurationError(Text2CypherError):
    """Raised when required runtime configuration is invalid or absent."""


class QuestionValidationError(Text2CypherError):
    """Raised when a user question is empty or otherwise invalid."""


class SchemaFetchError(Text2CypherError):
    """Raised when the graph schema cannot be fetched."""


class PromptBuildError(Text2CypherError):
    """Raised when a prompt cannot be built."""


class LLMGenerationError(Text2CypherError):
    """Raised when the LLM request or response is invalid."""


class CypherParseError(Text2CypherError):
    """Raised when no usable Cypher can be extracted from LLM output."""


class CypherValidationError(Text2CypherError):
    """Raised when a Cypher query is unsafe or not read-only."""


class CypherExecutionError(Text2CypherError):
    """Raised when Neo4j cannot execute an approved query."""


class BootstrapNotReadyError(Text2CypherError):
    """Raised until real production adapters are connected."""

