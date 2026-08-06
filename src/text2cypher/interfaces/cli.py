"""接口层的 Text2Cypher 命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from text2cypher.application.bootstrap import build_pipeline
from text2cypher.application.pipeline import Text2CypherPipeline
from text2cypher.config import Settings
from text2cypher.domain.errors import Text2CypherError
from text2cypher.interfaces.logging import configure_logging

EXIT_SUCCESS = 0
EXIT_INPUT_OR_CONFIGURATION_ERROR = 2
EXIT_RUNTIME_ERROR = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="text2cypher",
        description=(
            "根据自然语言问题生成并安全执行只读 Cypher 查询。"
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    ask = subcommands.add_parser("ask", help="提出一个自然语言图谱问题。")
    ask.add_argument("question", help="要转换为 Cypher 的自然语言问题。")
    ask.add_argument(
        "--json",
        action="store_true",
        help="成功查询后输出结构化 JSON。",
    )
    return parser


def _print_error(message: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"错误：{message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """解析 CLI 输入并委托给真实运行流水线。"""

    args = _parser().parse_args(argv)
    if args.command != "ask":
        return EXIT_INPUT_OR_CONFIGURATION_ERROR

    try:
        settings = Settings.from_environment()
    except ValidationError:
        _print_error(
            "运行时配置不完整。请将 .env.example 复制为 .env，并提供全部 "
            "TEXT2CYPHER_* 配置。",
            args.json,
        )
        return EXIT_INPUT_OR_CONFIGURATION_ERROR

    configure_logging(settings.log_level)

    pipeline: Text2CypherPipeline | None = None
    try:
        pipeline = build_pipeline(settings)
        response = pipeline.run(args.question)
    except Text2CypherError as error:
        _print_error(str(error), args.json)
        return EXIT_RUNTIME_ERROR
    finally:
        if pipeline is not None:
            pipeline.close()

    if args.json:
        print(response.formatted)
    else:
        print(f"结果：\n{response.formatted}")
    return EXIT_SUCCESS
