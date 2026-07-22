"""组件层中从模型输出提取单条 Cypher 的保守解析器。"""

from __future__ import annotations

import re

from text2cypher.domain.errors import CypherParseError


class DefaultCypherParser:
    """提取首个围栏查询，或保留完整的裸响应。"""

    _fence = chr(96) * 3
    _fenced_query = re.compile(
        rf"{_fence}(?:cypher)?[ \t]*\r?\n?(.*?){_fence}",
        flags=re.IGNORECASE | re.DOTALL,
    )
    _bare_prefix = re.compile(r"^cypher(?:\s+query)?\s*:\s*", re.IGNORECASE)

    def parse(self, text: str) -> str:
        if not isinstance(text, str):
            raise CypherParseError("模型响应内容必须是文本")

        match = self._fenced_query.search(text)
        candidate = match.group(1) if match else text
        normalized = candidate.replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized = self._bare_prefix.sub("", normalized).strip()
        if normalized.endswith(";"):
            normalized = normalized[:-1].rstrip()
        if not normalized:
            raise CypherParseError("模型响应不包含 Cypher")
        return normalized
