"""隔离应用层与外部实现细节的领域端口。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from text2cypher.domain.models import (
    ChatPrompt,
    GraphSchema,
    LLMResponse,
    QueryResult,
    ValidationReport,
)


@runtime_checkable
class SchemaFetcher(Protocol):
    """获取实时图谱 Schema。"""

    def fetch(self) -> GraphSchema: ...


@runtime_checkable
class PromptBuilder(Protocol):
    """构造与模型服务商无关的聊天提示词。"""

    def build(self, schema: GraphSchema, question: str) -> ChatPrompt: ...


@runtime_checkable
class LLMClient(Protocol):
    """通过通用大模型 API 生成 Cypher。"""

    def generate(self, prompt: ChatPrompt) -> LLMResponse: ...


@runtime_checkable
class CypherParser(Protocol):
    """从模型输出提取一条规范化 Cypher。"""

    def parse(self, text: str) -> str: ...


@runtime_checkable
class CypherValidator(Protocol):
    """在执行前校验 Cypher 是否安全且只读。"""

    def validate(self, cypher: str) -> ValidationReport: ...


@runtime_checkable
class CypherExecutor(Protocol):
    """执行已经通过校验的只读 Cypher 查询。"""

    def execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult: ...


@runtime_checkable
class ResultFormatter(Protocol):
    """将查询结果格式化给用户或 CLI 使用者。"""

    def format(self, question: str, cypher: str, result: QueryResult) -> str: ...
