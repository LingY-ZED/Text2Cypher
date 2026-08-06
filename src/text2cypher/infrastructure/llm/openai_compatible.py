"""基础设施层的 OpenAI 兼容聊天补全同步客户端。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import sleep
from typing import Any

import httpx

from text2cypher.components.recovery_logging import log_retry_event
from text2cypher.components.retry import (
    RetryableOperationError,
    RetryExecutor,
    RetryPolicy,
)
from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import ChatPrompt, LLMResponse

_LOGGER = logging.getLogger(__name__)


class OpenAICompatibleLLMClient:
    """通过 OpenAI 兼容接口生成 Cypher，且不泄露提示词或密钥。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        max_tokens: int = 512,
        disable_thinking: bool = False,
        retry_policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep_func: Callable[[float], None] = sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._disable_thinking = disable_thinking
        self._retry_executor = RetryExecutor(
            retry_policy or RetryPolicy(),
            sleep=sleep_func,
            on_event=lambda event: log_retry_event(
                _LOGGER,
                component="llm",
                stage="chat_completion",
                retry_event=event,
            ),
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._client = client or httpx.Client(
            timeout=float(timeout_seconds),
            transport=transport,
        )
        self._owns_client = client is None

    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        """发送确定性聊天补全请求并提取首个文本结果。"""

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        if self._disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        return self._retry_executor.run(
            lambda: self._generate_once(payload)
        )

    def _generate_once(self, payload: dict[str, Any]) -> LLMResponse:
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            self._raise_retryable("模型请求超时", "timeout")
        except httpx.HTTPStatusError as error:
            message = f"模型服务返回 HTTP {error.response.status_code}"
            if error.response.status_code in {
                408,
                409,
                425,
                429,
                500,
                502,
                503,
                504,
            }:
                self._raise_retryable(
                    message,
                    f"http_{error.response.status_code}",
                    retry_after_seconds=self._retry_after_seconds(error.response),
                )
            raise LLMGenerationError(message) from None
        except httpx.RequestError:
            self._raise_retryable("无法连接模型服务", "request_error")

        try:
            body = response.json()
        except ValueError:
            self._raise_retryable("模型服务返回了无效 JSON", "invalid_json")
        try:
            content = self._extract_content(body)
        except LLMGenerationError as error:
            raise RetryableOperationError(
                error,
                reason="invalid_response",
            ) from None
        model = body.get("model") if isinstance(body, dict) else None
        finish_reason = self._extract_finish_reason(body)
        return LLMResponse(
            content=content,
            model=model if isinstance(model, str) else self._model,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _raise_retryable(
        message: str,
        reason: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        raise RetryableOperationError(
            LLMGenerationError(message),
            reason=reason,
            retry_after_seconds=retry_after_seconds,
        )

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, IndexError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0.0, (parsed - self._now()).total_seconds())

    def close(self) -> None:
        """关闭由当前实例创建的 HTTP 客户端。"""

        if self._owns_client:
            self._client.close()

    @staticmethod
    def _extract_content(body: Any) -> str:
        if not isinstance(body, dict):
            raise LLMGenerationError("模型响应格式不合法")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMGenerationError("模型响应不包含候选结果")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMGenerationError("模型候选结果格式不合法")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise LLMGenerationError("模型响应不包含消息内容")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMGenerationError("模型响应不包含文本内容")
        return content

    @staticmethod
    def _extract_finish_reason(body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return None
        finish_reason = first_choice.get("finish_reason")
        return finish_reason if isinstance(finish_reason, str) else None
