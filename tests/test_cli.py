from __future__ import annotations

import pytest

from text2cypher.cli import EXIT_NOT_READY, main


def _set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "TEXT2CYPHER_NEO4J_URI": "bolt://localhost:7687",
        "TEXT2CYPHER_NEO4J_USERNAME": "neo4j",
        "TEXT2CYPHER_NEO4J_PASSWORD": "secret",
        "TEXT2CYPHER_LLM_BASE_URL": "https://example.test/v1",
        "TEXT2CYPHER_LLM_API_KEY": "key",
        "TEXT2CYPHER_LLM_MODEL": "demo",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_cli_reports_unwired_adapters_after_valid_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_required_environment(monkeypatch)

    exit_code = main(["ask", "列出所有微服务", "--json"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_NOT_READY
    assert "not implemented yet" in captured.err


def test_cli_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--help"])

    assert "text2cypher" in capsys.readouterr().out
