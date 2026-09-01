"""CLI 的 JSON 结构化日志初始化。"""

from __future__ import annotations

import json
import logging
from typing import Any


class JsonLogFormatter(logging.Formatter):
    """将恢复事件写为不干扰 stdout 的单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        recovery_event = getattr(record, "recovery_event", None)
        if isinstance(recovery_event, dict):
            payload.update(recovery_event)
        decomposition_review_event = getattr(
            record,
            "decomposition_review_event",
            None,
        )
        if isinstance(decomposition_review_event, dict):
            payload.update(decomposition_review_event)
        primary_agent_event = getattr(record, "primary_agent_event", None)
        if isinstance(primary_agent_event, dict):
            payload.update(primary_agent_event)
        result_summary_event = getattr(record, "result_summary_event", None)
        if isinstance(result_summary_event, dict):
            payload.update(result_summary_event)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    """在 CLI 进程中初始化 stderr JSON 日志，已有宿主配置优先。"""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(
        level=getattr(logging, level),
        handlers=[handler],
    )
