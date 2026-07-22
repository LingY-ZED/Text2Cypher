"""基础设施层的 OpenAI 兼容聊天补全同步客户端。"""

from __future__ import annotations

from typing import Any

import httpx

from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import ChatPrompt, LLMResponse


class OpenAICompatibleLLMClient:
    """通过 OpenAI 兼容接口生成 Cypher，且不泄露提示词或密钥。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
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
            "stream": False,
        }
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            raise LLMGenerationError("模型请求超时") from None
        except httpx.HTTPStatusError as error:
            raise LLMGenerationError(
                f"模型服务返回 HTTP {error.response.status_code}"
            ) from None
        except httpx.RequestError:
            raise LLMGenerationError("无法连接模型服务") from None

        try:
            body = response.json()
        except ValueError:
            raise LLMGenerationError("模型服务返回了无效 JSON") from None
        content = self._extract_content(body)
        model = body.get("model") if isinstance(body, dict) else None
        finish_reason = self._extract_finish_reason(body)
        return LLMResponse(
            content=content,
            model=model if isinstance(model, str) else self._model,
            finish_reason=finish_reason,
        )

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
