"""依赖子问题使用的 Cypher 参数白名单守卫。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from text2cypher.domain.errors import DependencyParameterValidationError
from text2cypher.domain.models import DependencyParameter

_PARAMETER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_UNWIND_PARAMETER_PATTERN = re.compile(
    r"\bUNWIND\s+\$([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+([A-Za-z_]\w*)",
    re.IGNORECASE,
)


class CypherParameterGuard:
    """确保模型只使用并完整使用系统声明的依赖参数。"""

    def validate(
        self,
        cypher: str,
        parameters: Mapping[str, Any],
        specifications: tuple[DependencyParameter, ...],
    ) -> None:
        expected_names = tuple(specification.name for specification in specifications)
        if len(set(expected_names)) != len(expected_names):
            raise ValueError("依赖参数规格不能重名")
        provided_names = set(parameters)
        if provided_names != set(expected_names):
            raise ValueError("依赖参数值与规格不一致")

        actual_names = set(
            _PARAMETER_PATTERN.findall(self._without_literals(cypher))
        )
        missing_names = set(expected_names) - actual_names
        unknown_names = actual_names - set(expected_names)
        if unknown_names:
            raise DependencyParameterValidationError("Cypher 使用了未绑定参数")
        if missing_names:
            raise DependencyParameterValidationError("Cypher 未使用全部依赖参数")

        unwound_aliases = self._unwound_aliases(cypher)
        unwound_names = set(unwound_aliases)
        if set(expected_names) - unwound_names:
            raise DependencyParameterValidationError(
                "Cypher 未使用 UNWIND 展开全部依赖参数"
            )
        self._validate_column_reads(specifications, unwound_aliases, cypher)

    @staticmethod
    def _unwound_aliases(cypher: str) -> dict[str, tuple[str, ...]]:
        aliases: dict[str, list[str]] = {}
        for parameter_name, alias in _UNWIND_PARAMETER_PATTERN.findall(
            CypherParameterGuard._without_literals(cypher)
        ):
            aliases.setdefault(parameter_name, []).append(alias)
        return {
            parameter_name: tuple(parameter_aliases)
            for parameter_name, parameter_aliases in aliases.items()
        }

    @staticmethod
    def _validate_column_reads(
        specifications: tuple[DependencyParameter, ...],
        unwound_aliases: Mapping[str, tuple[str, ...]],
        cypher: str,
    ) -> None:
        searchable_cypher = CypherParameterGuard._without_string_literals(cypher)
        for specification in specifications:
            aliases = unwound_aliases[specification.name]
            for column in specification.columns:
                if any(
                    CypherParameterGuard._reads_column(
                        searchable_cypher,
                        alias,
                        column,
                    )
                    for alias in aliases
                ):
                    continue
                raise DependencyParameterValidationError(
                    "Cypher 未读取全部依赖参数列"
                )

    @staticmethod
    def _reads_column(cypher: str, alias: str, column: str) -> bool:
        escaped_alias = re.escape(alias)
        escaped_column = re.escape(column)
        pattern = re.compile(
            rf"\b{escaped_alias}\.(?:`{escaped_column}`|{escaped_column})(?!\w)"
        )
        return pattern.search(cypher) is not None

    @staticmethod
    def _without_literals(cypher: str) -> str:
        """清除字符串和反引号标识符，避免把文本中的 `$name` 误判为参数。"""

        output: list[str] = []
        quote: str | None = None
        in_identifier = False
        index = 0
        while index < len(cypher):
            current = cypher[index]
            next_character = cypher[index + 1] if index + 1 < len(cypher) else ""
            if quote is not None:
                output.append(" ")
                if current == "\\" and next_character:
                    output.append(" ")
                    index += 2
                    continue
                if current == quote:
                    quote = None
                index += 1
                continue
            if in_identifier:
                output.append(" ")
                if current == chr(96):
                    in_identifier = False
                index += 1
                continue
            if current in ("'", '"'):
                quote = current
                output.append(" ")
                index += 1
                continue
            if current == chr(96):
                in_identifier = True
                output.append(" ")
                index += 1
                continue
            output.append(current)
            index += 1
        return "".join(output)

    @staticmethod
    def _without_string_literals(cypher: str) -> str:
        output: list[str] = []
        quote: str | None = None
        index = 0
        while index < len(cypher):
            current = cypher[index]
            next_character = cypher[index + 1] if index + 1 < len(cypher) else ""
            if quote is not None:
                output.append(" ")
                if current == "\\" and next_character:
                    output.append(" ")
                    index += 2
                    continue
                if current == quote:
                    quote = None
                index += 1
                continue
            if current in ("'", '"'):
                quote = current
                output.append(" ")
                index += 1
                continue
            output.append(current)
            index += 1
        return "".join(output)
