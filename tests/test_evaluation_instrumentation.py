"""Evaluation telemetry must not retain prompts or model response bodies."""

from __future__ import annotations

import json

import pytest

from evaluation.instrumentation import EvaluationRecorder, StageLLMClient
from text2cypher.domain.models import ChatPrompt, LLMResponse


class _SuccessfulClient:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        return LLMResponse(
            content="sensitive-model-response",
            model="model",
            finish_reason="stop",
        )

    def close(self) -> None:
        return None


class _FailingClient:
    def generate(self, prompt: ChatPrompt) -> LLMResponse:
        del prompt
        raise RuntimeError("sensitive-response-body")

    def close(self) -> None:
        return None


def test_llm_events_store_metadata_without_prompt_or_response_content() -> None:
    recorder = EvaluationRecorder()
    client = StageLLMClient(_SuccessfulClient(), recorder, "generation")

    client.generate(ChatPrompt(system="sensitive-system", user="sensitive-user"))
    serialized = json.dumps(recorder.events)

    assert "sensitive-system" not in serialized
    assert "sensitive-user" not in serialized
    assert "sensitive-model-response" not in serialized
    assert recorder.events[0]["content_length"] == len("sensitive-model-response")


def test_llm_failure_event_does_not_store_exception_response_body() -> None:
    recorder = EvaluationRecorder()
    client = StageLLMClient(_FailingClient(), recorder, "router")

    with pytest.raises(RuntimeError):
        client.generate(ChatPrompt(system="system", user="user"))

    serialized = json.dumps(recorder.events)
    assert "sensitive-response-body" not in serialized
    assert recorder.events == [
        {
            "component": "llm",
            "stage": "router",
            "outcome": "failed",
            "duration_seconds": recorder.events[0]["duration_seconds"],
            "error_type": "RuntimeError",
            "query_index": 0,
        }
    ]
