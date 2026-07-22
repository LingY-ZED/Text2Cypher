"""Small, intentionally conservative Cypher output parser."""

from __future__ import annotations

import re

from text2cypher.errors import CypherParseError


class DefaultCypherParser:
    """Extract the first fenced query or preserve the complete bare response."""

    _fence = chr(96) * 3
    _fenced_query = re.compile(
        rf"{_fence}(?:cypher)?[ \t]*\r?\n?(.*?){_fence}",
        flags=re.IGNORECASE | re.DOTALL,
    )
    _bare_prefix = re.compile(r"^cypher(?:\s+query)?\s*:\s*", re.IGNORECASE)

    def parse(self, text: str) -> str:
        if not isinstance(text, str):
            raise CypherParseError("LLM response content must be text")

        match = self._fenced_query.search(text)
        candidate = match.group(1) if match else text
        normalized = candidate.replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized = self._bare_prefix.sub("", normalized).strip()
        if normalized.endswith(";"):
            normalized = normalized[:-1].rstrip()
        if not normalized:
            raise CypherParseError("LLM response did not contain Cypher")
        return normalized

