from __future__ import annotations

import json

from text2cypher.application.iterative_answerer import LLMIterativeAnswerer
from text2cypher.components.iterative_answerer import IterativeAnswerPromptBuilder
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.iterative import (
    IterativeAnswerContext,
    IterativeStopReason,
)
from text2cypher.domain.models import (
    LLMResponse,
    ResultSummaryFallbackReason,
    ResultSummaryMode,
)


class _StubLLMClient:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self._response = response
        self.prompts: list[object] = []

    def generate(self, prompt: object) -> LLMResponse:
        self.prompts.append(prompt)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _context() -> IterativeAnswerContext:
    return IterativeAnswerContext(
        original_question="查询支付服务",
        observations=(),
        obtained_information=("没有匹配记录",),
        missing_information=("服务实现位置",),
        stop_reason=IterativeStopReason.ACTION_BUDGET_EXHAUSTED,
    )


def test_answerer_generates_one_valid_llm_answer() -> None:
    client = _StubLLMClient(LLMResponse('{"answer":"当前未找到匹配服务。"}'))

    summary = LLMIterativeAnswerer(client).answer(_context())

    assert summary.mode is ResultSummaryMode.LLM
    assert len(client.prompts) == 1


def test_answerer_uses_partial_template_on_expected_llm_failure() -> None:
    client = _StubLLMClient(LLMGenerationError("provider unavailable"))

    summary = LLMIterativeAnswerer(client).answer(_context())

    assert summary.mode is ResultSummaryMode.TEMPLATE
    assert summary.fallback_reason is ResultSummaryFallbackReason.LLM_FAILURE
    assert "部分答案" in summary.answer
    assert "action_budget_exhausted" in summary.answer


def test_answer_prompt_uses_only_compact_terminal_state() -> None:
    prompt = IterativeAnswerPromptBuilder().build(_context())
    payload = json.loads(prompt.user.split("\n\n")[1])

    assert payload["stop_reason"] == "action_budget_exhausted"
    assert "不可信数据" in prompt.system
    assert "部分答案" in prompt.system
