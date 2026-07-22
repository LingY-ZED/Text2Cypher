from __future__ import annotations

import json

import httpx
import pytest

from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import ChatPrompt
from text2cypher.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient


def test_llm_client_sends_openai_compatible_request_and_extracts_response() -> None:
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        received["authorization"] = request.headers["Authorization"]
        received["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {"content": "RETURN 1 AS 数值"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = OpenAICompatibleLLMClient(
        base_url="https://llm.example/v1/",
        api_key="test-api-key",
        model="deepseek-v4-flash",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    response = client.generate(ChatPrompt(system="系统提示", user="用户问题"))
    client.close()

    assert response.content == "RETURN 1 AS 数值"
    assert response.model == "deepseek-v4-flash"
    assert response.finish_reason == "stop"
    assert received == {
        "url": "https://llm.example/v1/chat/completions",
        "authorization": "Bearer test-api-key",
        "payload": {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "用户问题"},
            ],
            "temperature": 0,
            "stream": False,
        },
    }


def test_llm_client_hides_response_body_when_service_returns_error() -> None:
    client = OpenAICompatibleLLMClient(
        base_url="https://llm.example/v1",
        api_key="test-api-key",
        model="test-model",
        timeout_seconds=5,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, text="不应泄露的服务响应")
        ),
    )

    with pytest.raises(LLMGenerationError, match="503") as error:
        client.generate(ChatPrompt(system="系统提示", user="用户问题"))

    client.close()
    assert "不应泄露的服务响应" not in str(error.value)
    assert "test-api-key" not in str(error.value)
