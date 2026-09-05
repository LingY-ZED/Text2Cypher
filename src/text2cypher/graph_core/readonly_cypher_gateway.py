"""候选 Cypher 的确定性只读准入和执行入口。"""

from __future__ import annotations

from text2cypher.domain.errors import (
    CypherExecutionError,
    CypherParseError,
    CypherValidationError,
)
from text2cypher.domain.models import ExecutedCypher
from text2cypher.domain.ports import CypherExecutor, CypherParser, CypherValidator


class CandidateExecutionFailure(Exception):
    """保留可恢复错误对应候选，供编排层构造定向修正输入。"""

    def __init__(
        self,
        candidate: str,
        error: CypherParseError | CypherValidationError | CypherExecutionError,
    ) -> None:
        super().__init__(str(error))
        self.candidate = candidate
        self.error = error


class DefaultReadOnlyCypherGateway:
    """按 Parser、Validator、Executor 固定顺序执行一个模型候选。"""

    def __init__(
        self,
        parser: CypherParser,
        validator: CypherValidator,
        executor: CypherExecutor,
    ) -> None:
        self._parser = parser
        self._validator = validator
        self._executor = executor

    def execute_candidate(self, candidate: str) -> ExecutedCypher:
        """执行候选，并保留可修复失败发生时的准确候选文本。"""

        try:
            cypher = self._parser.parse(candidate)
        except CypherParseError as error:
            raise CandidateExecutionFailure(candidate, error) from error
        try:
            self._validator.validate(cypher)
            result = self._executor.execute(cypher)
        except (CypherValidationError, CypherExecutionError) as error:
            raise CandidateExecutionFailure(cypher, error) from error
        return ExecutedCypher(cypher=cypher, result=result)
