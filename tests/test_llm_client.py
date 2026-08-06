from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from text2cypher.domain.errors import LLMGenerationError
from text2cypher.domain.models import ChatPrompt
from text2cypher.infrastructure.llm.openai_compatible import OpenAICompatibleLLMClient


def _client(
    handler: httpx.MockTransport | httpx.BaseTransport,
    *,
    sleeps: list[float] | None = None,
    now: datetime | None = None,
) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(
        base_url="https://llm.example/v1",
        api_key="test-api-key",
        model="test-model",
        timeout_seconds=5,
        transport=handler,
        sleep_func=(sleeps.append if sleeps is not None else lambda _: None),
        now=(lambda: now) if now is not None else None,
    )


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
            "max_tokens": 512,
            "stream": False,
        },
    }


def test_llm_client_hides_response_body_when_service_returns_error() -> None:
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(503, text="不应泄露的服务响应")
        ),
    )

    with pytest.raises(LLMGenerationError, match="503") as error:
        client.generate(ChatPrompt(system="系统提示", user="用户问题"))

    client.close()
    assert "不应泄露的服务响应" not in str(error.value)
    assert "test-api-key" not in str(error.value)


def test_llm_client_retries_transient_status_before_success() -> None:
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, text="sensitive")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "RETURN 1"}}]},
        )

    client = _client(httpx.MockTransport(handler), sleeps=waits)

    response = client.generate(ChatPrompt(system="系统提示", user="用户问题"))
    client.close()

    assert response.content == "RETURN 1"
    assert calls == 3
    assert waits == [0.5, 1.0]


def test_llm_retry_logs_do_not_include_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="不应出现在日志中的响应正文")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "RETURN 1"}}]},
        )

    caplog.set_level(logging.WARNING)
    client = _client(httpx.MockTransport(handler))

    client.generate(ChatPrompt(system="系统提示", user="敏感问题"))
    client.close()

    events = [
        record.recovery_event
        for record in caplog.records
        if hasattr(record, "recovery_event")
    ]
    assert [event["event"] for event in events] == [
        "retry_scheduled",
        "retry_succeeded",
    ]
    assert all(event["component"] == "llm" for event in events)
    assert all(event["stage"] == "chat_completion" for event in events)
    assert "敏感问题" not in caplog.text
    assert "不应出现在日志中的响应正文" not in caplog.text


def test_llm_client_retries_blank_content_before_success() -> None:
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = " " if calls == 1 else "RETURN 1"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    client = _client(httpx.MockTransport(handler), sleeps=waits)

    response = client.generate(ChatPrompt(system="系统提示", user="用户问题"))
    client.close()

    assert response.content == "RETURN 1"
    assert calls == 2
    assert waits == [0.5]


def test_llm_client_does_not_retry_non_transient_http_status() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="sensitive")

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(LLMGenerationError, match="401"):
        client.generate(ChatPrompt(system="系统提示", user="用户问题"))

    client.close()
    assert calls == 1


def test_llm_client_honors_retry_after_date_with_maximum_delay() -> None:
    calls = 0
    waits: list[float] = []
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "Thu, 01 Jan 2026 00:00:09 GMT"},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "RETURN 1"}}]},
        )

    client = _client(httpx.MockTransport(handler), sleeps=waits, now=now)

    client.generate(ChatPrompt(system="系统提示", user="用户问题"))
    client.close()

    assert waits == [4.0]


def test_llm_client_only_sends_thinking_extension_when_enabled() -> None:
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "RETURN 1"}}]},
        )

    client = OpenAICompatibleLLMClient(
        base_url="https://llm.example/v1",
        api_key="test-api-key",
        model="test-model",
        timeout_seconds=5,
        disable_thinking=True,
        transport=httpx.MockTransport(handler),
    )

    client.generate(ChatPrompt(system="系统提示", user="用户问题"))
    client.close()

    assert received["thinking"] == {"type": "disabled"}
