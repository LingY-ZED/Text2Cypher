from __future__ import annotations

import pytest

from text2cypher.cypher_parser import DefaultCypherParser
from text2cypher.errors import CypherParseError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MATCH (n) RETURN n;", "MATCH (n) RETURN n"),
        ("Cypher query: RETURN 1;", "RETURN 1"),
        (
            f"{chr(96) * 3}cypher\nMATCH (n) RETURN n;\n{chr(96) * 3}",
            "MATCH (n) RETURN n",
        ),
    ],
)
def test_parser_normalizes_supported_response_shapes(raw: str, expected: str) -> None:
    assert DefaultCypherParser().parse(raw) == expected


def test_parser_rejects_empty_response() -> None:
    with pytest.raises(CypherParseError):
        DefaultCypherParser().parse("  ")

