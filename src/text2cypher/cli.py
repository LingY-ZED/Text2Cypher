"""CLI-first entry point for the Text2Cypher skeleton."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from text2cypher.bootstrap import build_pipeline
from text2cypher.config import Settings
from text2cypher.errors import BootstrapNotReadyError, Text2CypherError

EXIT_SUCCESS = 0
EXIT_INPUT_OR_CONFIGURATION_ERROR = 2
EXIT_NOT_READY = 3
EXIT_RUNTIME_ERROR = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="text2cypher",
        description=(
            "Generate and safely execute read-only Cypher from a "
            "natural-language question."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    ask = subcommands.add_parser("ask", help="Ask a natural-language graph question.")
    ask.add_argument("question", help="Natural-language question to convert to Cypher.")
    ask.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON on a successful future query.",
    )
    return parser


def _print_error(message: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"Error: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI input and delegate to the future live pipeline."""

    args = _parser().parse_args(argv)
    if args.command != "ask":
        return EXIT_INPUT_OR_CONFIGURATION_ERROR

    try:
        settings = Settings.from_environment()
    except ValidationError:
        _print_error(
            "Runtime configuration is incomplete. Copy .env.example to .env "
            "and provide all TEXT2CYPHER_* values.",
            args.json,
        )
        return EXIT_INPUT_OR_CONFIGURATION_ERROR

    try:
        pipeline = build_pipeline(settings)
        response = pipeline.run(args.question)
    except BootstrapNotReadyError as error:
        _print_error(str(error), args.json)
        return EXIT_NOT_READY
    except Text2CypherError as error:
        _print_error(str(error), args.json)
        return EXIT_RUNTIME_ERROR

    if args.json:
        print(response.formatted)
    else:
        print(f"Cypher:\n{response.cypher}\n\nResults:\n{response.formatted}")
    return EXIT_SUCCESS
