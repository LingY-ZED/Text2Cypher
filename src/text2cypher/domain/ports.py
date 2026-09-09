"""隔离应用层与外部实现细节的领域端口。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureContext,
    ExecutedCypher,
    FewShotExample,
    GraphQueryRequest,
    GraphSchema,
    LLMResponse,
    PrimaryAgentPlan,
    PrimaryAgentQuery,
    QueryContext,
    QueryResult,
    QueryStatement,
    QuestionDecomposition,
    ResultSummary,
    SubQueryResponse,
    ValidationReport,
)


@runtime_checkable
class SchemaFetcher(Protocol):
    """获取实时图谱 Schema。"""

    def fetch(self) -> GraphSchema: ...


@runtime_checkable
class PromptBuilder(Protocol):
    """构造与模型服务商无关的聊天提示词。"""

    def build(
        self,
        schema: GraphSchema,
        question: str,
        examples: tuple[FewShotExample, ...] = (),
    ) -> ChatPrompt: ...


@runtime_checkable
class PlannedPromptBuilder(Protocol):
    """可消费 Primary 完整查询计划的 Translator Prompt 端口。"""

    def build_planned(
        self,
        schema: GraphSchema,
        query: PrimaryAgentQuery,
        examples: tuple[FewShotExample, ...] = (),
    ) -> ChatPrompt: ...


@runtime_checkable
class FewShotRouter(Protocol):
    """根据问题和实时 Schema 路由可用于最终 Prompt 的示例。"""

    def route(
        self,
        question: str,
        schema: GraphSchema,
    ) -> tuple[FewShotExample, ...]: ...


@runtime_checkable
class PlannedFewShotRouter(Protocol):
    """可消费 Primary 完整查询计划的 Few-shot 路由端口。"""

    def route_planned(
        self,
        query: PrimaryAgentQuery,
        schema: GraphSchema,
    ) -> tuple[FewShotExample, ...]: ...


@runtime_checkable
class QuestionDecomposer(Protocol):
    """把问题规划为一到三个互相独立的子问题。"""

    def decompose(
        self,
        question: str,
        schema: GraphSchema,
    ) -> QuestionDecomposition: ...


@runtime_checkable
class PrimaryAgent(Protocol):
    """把用户问题规划为单轮结构化自然语言查询。"""

    def plan(self, question: str) -> PrimaryAgentPlan: ...


@runtime_checkable
class LLMClient(Protocol):
    """通过通用大模型 API 生成 Cypher。"""

    def generate(self, prompt: ChatPrompt) -> LLMResponse: ...


@runtime_checkable
class CypherCorrector(Protocol):
    """基于初始完整 Prompt 和一次失败候选生成修正版模型输出。"""

    def correct(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure: CypherFailureContext,
    ) -> LLMResponse: ...


@runtime_checkable
class CypherParser(Protocol):
    """从模型输出提取一条规范化 Cypher。"""

    def parse(self, text: str) -> str: ...


@runtime_checkable
class CypherValidator(Protocol):
    """在执行前校验 Cypher 是否安全且只读。"""

    def validate(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ValidationReport: ...


@runtime_checkable
class CypherExecutor(Protocol):
    """执行已经通过校验的只读 Cypher 查询。"""

    def execute(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> QueryResult: ...


@runtime_checkable
class ReadOnlyCypherGateway(Protocol):
    """让候选 Cypher 经过解析、只读准入和执行的唯一入口。"""

    def execute_candidate(self, candidate: str) -> ExecutedCypher: ...


@runtime_checkable
class ParameterizedReadOnlyCypherGateway(ReadOnlyCypherGateway, Protocol):
    """执行由确定性编译器生成的参数化只读语句。"""

    def execute_statement(self, statement: QueryStatement) -> ExecutedCypher: ...


@runtime_checkable
class GraphQueryEngine(Protocol):
    """执行一条独立的图查询翻译、准入和恢复流程。"""

    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher: ...


@runtime_checkable
class SchemaTool(Protocol):
    """向 Runtime 提供固定 Schema 获取能力的内部 Tool。"""

    def get_schema(self) -> GraphSchema: ...


@runtime_checkable
class CodeGraphQueryTool(Protocol):
    """向 Runtime 提供一次图查询能力的内部 Tool。"""

    def query(
        self,
        request: GraphQueryRequest,
        context: QueryContext,
    ) -> ExecutedCypher: ...


@runtime_checkable
class ResultFormatter(Protocol):
    """将查询结果格式化给用户或 CLI 使用者。"""

    def format(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
        summary: ResultSummary | None = None,
    ) -> str: ...


@runtime_checkable
class ResultSummarizer(Protocol):
    """将已成功执行的子查询结果总结为自然语言答案。"""

    def summarize(
        self,
        question: str,
        sub_queries: tuple[SubQueryResponse, ...],
    ) -> ResultSummary: ...


class CallChainTool(Protocol):
    """执行完整调用链，None 表示能力未命中。"""

    def query(self, query: PrimaryAgentQuery) -> ExecutedCypher | None: ...
