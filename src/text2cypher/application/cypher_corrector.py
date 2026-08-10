"""使用共享模型客户端执行一次性 Cypher 纠错。"""

from __future__ import annotations

from text2cypher.components.cypher_correction import CypherCorrectionPromptBuilder
from text2cypher.domain.models import (
    ChatPrompt,
    CypherFailureContext,
    LLMResponse,
)
from text2cypher.domain.ports import LLMClient


class LLMCypherCorrector:
    """把初始 Prompt 与失败候选交给共享模型客户端进行受限纠错。"""

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_builder: CypherCorrectionPromptBuilder | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_builder = prompt_builder or CypherCorrectionPromptBuilder()

    def correct(
        self,
        base_prompt: ChatPrompt,
        failed_candidate: str,
        failure: CypherFailureContext,
    ) -> LLMResponse:
        """生成一条待重新走完整安全链路的修正版模型输出。"""

        prompt = self._prompt_builder.build(
            base_prompt,
            failed_candidate,
            failure,
        )
        return self._llm_client.generate(prompt)
