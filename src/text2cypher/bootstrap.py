"""Future production-adapter assembly point."""

from __future__ import annotations

from text2cypher.config import Settings
from text2cypher.errors import BootstrapNotReadyError
from text2cypher.pipeline import Text2CypherPipeline


def build_pipeline(settings: Settings) -> Text2CypherPipeline:
    """Build the live pipeline once the adapters in todo.md are implemented."""

    del settings
    raise BootstrapNotReadyError(
        "Live Neo4j and LLM adapters are not implemented yet. "
        "Complete the P0 items in todo.md before running queries."
    )

