from __future__ import annotations

import pytest

from text2cypher.domain.models import (
    QueryResult,
    SubQueryError,
    SubQueryResponse,
    SubQueryStatus,
    Text2CypherResponse,
    Text2CypherStatus,
)
from text2cypher.interfaces.cli import EXIT_PARTIAL_SUCCESS, EXIT_SUCCESS, main


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


def test_cli_runs_and_closes_pipeline_after_valid_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_required_environment(monkeypatch)

    class FakePipeline:
        closed = False

        def run(self, question: str) -> Text2CypherResponse:
            assert question == "列出所有微服务"
            return Text2CypherResponse(
                question=question,
                sub_queries=(
                    SubQueryResponse(
                        question=question,
                        cypher="RETURN 1 AS value",
                        result=QueryResult(
                            columns=("value",),
                            rows=({"value": 1},),
                        ),
                    ),
                ),
                formatted="{\"value\": 1}",
            )

        def close(self) -> None:
            self.closed = True

    pipeline = FakePipeline()
    monkeypatch.setattr(
        "text2cypher.interfaces.cli.build_pipeline",
        lambda settings: pipeline,
    )

    exit_code = main(["ask", "列出所有微服务", "--json"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert captured.out.strip() == "{\"value\": 1}"
    assert pipeline.closed is True


def test_cli_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--help"])

    assert "text2cypher" in capsys.readouterr().out


def test_cli_returns_partial_exit_code_without_polluting_json_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_environment(monkeypatch)

    class FakePipeline:
        def run(self, question: str) -> Text2CypherResponse:
            return Text2CypherResponse(
                question=question,
                sub_queries=(
                    SubQueryResponse(
                        question="成功分支",
                        cypher="RETURN 1 AS value",
                        result=QueryResult(("value",), ({"value": 1},)),
                    ),
                    SubQueryResponse(
                        question="失败分支",
                        cypher=None,
                        result=None,
                        id="q2",
                        status=SubQueryStatus.FAILED,
                        error=SubQueryError(
                            "generation_failed",
                            "子查询未能安全完成执行",
                        ),
                    ),
                ),
                formatted='{"status":"partial"}',
                status=Text2CypherStatus.PARTIAL,
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "text2cypher.interfaces.cli.build_pipeline",
        lambda settings: FakePipeline(),
    )

    exit_code = main(["ask", "组合查询", "--json"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_PARTIAL_SUCCESS
    assert captured.out.strip() == '{"status":"partial"}'
    assert captured.err == ""


def test_cli_reports_partial_completion_to_stderr_for_text_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_environment(monkeypatch)

    class FakePipeline:
        def run(self, question: str) -> Text2CypherResponse:
            return Text2CypherResponse(
                question=question,
                sub_queries=(
                    SubQueryResponse(
                        question="成功分支",
                        cypher="RETURN 1 AS value",
                        result=QueryResult(("value",), ({"value": 1},)),
                    ),
                    SubQueryResponse(
                        question="阻塞分支",
                        cypher=None,
                        result=None,
                        id="q2",
                        status=SubQueryStatus.BLOCKED,
                        error=SubQueryError(
                            "dependency_failed",
                            "依赖子查询未成功完成",
                        ),
                    ),
                ),
                formatted="partial result",
                status=Text2CypherStatus.PARTIAL,
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "text2cypher.interfaces.cli.build_pipeline",
        lambda settings: FakePipeline(),
    )

    exit_code = main(["ask", "组合查询"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_PARTIAL_SUCCESS
    assert captured.out == "结果：\npartial result\n"
    assert "部分子查询未完成" in captured.err
