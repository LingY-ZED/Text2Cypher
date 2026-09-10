"""将 Tool 原始结果投影为可审计、可规划的紧凑 Observation。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from text2cypher.domain.iterative import (
    KeyEntity,
    Observation,
    ObservationStatus,
    PlannerObservation,
    RawObservationResult,
    RuntimeToolName,
)
from text2cypher.domain.models import ExecutedCypher, ResolvedMethod

_SENSITIVE_COLUMNS = frozenset({"cypher", "query", "查询语句", "错误堆栈", "traceback"})


class ObservationCompactor:
    """保留原始结果，向 LLM 仅公开受限实体和摘要。"""

    def __init__(
        self,
        *,
        max_key_entities: int = 12,
        max_value_chars: int = 240,
        max_collection_items: int = 8,
    ) -> None:
        if type(max_key_entities) is not int or max_key_entities <= 0:
            raise ValueError("max_key_entities必须是正整数")
        if type(max_value_chars) is not int or max_value_chars <= 0:
            raise ValueError("max_value_chars必须是正整数")
        if type(max_collection_items) is not int or max_collection_items <= 0:
            raise ValueError("max_collection_items必须是正整数")
        self._max_key_entities = max_key_entities
        self._max_value_chars = max_value_chars
        self._max_collection_items = max_collection_items

    def result(
        self,
        *,
        observation_id: str,
        action_id: str,
        tool: RuntimeToolName,
        raw_result: RawObservationResult,
        action_fingerprint: str,
    ) -> Observation:
        rows, columns, truncated = self._rows(raw_result)
        status = ObservationStatus.SUCCESS if rows else ObservationStatus.EMPTY
        entities = self._entities(observation_id, rows)
        evidence = self._evidence(entities, rows, action_fingerprint)
        return Observation(
            observation_id=observation_id,
            action_id=action_id,
            tool=tool,
            status=status,
            summary=self._result_summary(tool, len(rows), columns, truncated),
            row_count=len(rows),
            columns=columns,
            truncated=truncated,
            key_entities=entities,
            evidence_fingerprints=evidence,
            raw_result=raw_result,
        )

    def failed(
        self,
        *,
        observation_id: str,
        action_id: str,
        tool: RuntimeToolName,
        error: Exception,
    ) -> Observation:
        return Observation(
            observation_id=observation_id,
            action_id=action_id,
            tool=tool,
            status=ObservationStatus.FAILED,
            summary="Tool执行失败，下一轮可根据错误类型调整查询计划。",
            row_count=0,
            columns=(),
            truncated=False,
            key_entities=(),
            evidence_fingerprints=(),
            raw_result=None,
            error_type=type(error).__name__,
            error_message="Tool执行失败；未向Planner暴露底层诊断。",
        )

    def duplicate(
        self,
        *,
        observation_id: str,
        action_id: str,
        tool: RuntimeToolName,
    ) -> Observation:
        return Observation(
            observation_id=observation_id,
            action_id=action_id,
            tool=tool,
            status=ObservationStatus.DUPLICATE,
            summary="Action与此前已处理请求相同，已被循环保护阻断。",
            row_count=0,
            columns=(),
            truncated=False,
            key_entities=(),
            evidence_fingerprints=(),
            raw_result=None,
        )

    @staticmethod
    def planner_view(observation: Observation) -> PlannerObservation:
        """显式逐字段投影，避免将 raw_result 或证据摘要送进 Prompt。"""

        return PlannerObservation(
            observation_id=observation.observation_id,
            action_id=observation.action_id,
            tool=observation.tool,
            status=observation.status,
            summary=observation.summary,
            row_count=observation.row_count,
            columns=observation.columns,
            truncated=observation.truncated,
            key_entities=observation.key_entities,
            error_type=observation.error_type,
            error_message=observation.error_message,
        )

    def _rows(
        self,
        raw_result: RawObservationResult,
    ) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...], bool]:
        if raw_result is None:
            return (), (), False
        if isinstance(raw_result, ExecutedCypher):
            result = raw_result.result
            return result.rows, result.columns, result.truncated
        rows = tuple(self._resolved_method_row(item) for item in raw_result)
        return rows, (
            "qualified_name",
            "method_name",
            "class_qualified_name",
            "service_name",
            "graph_version",
        ), False

    @staticmethod
    def _resolved_method_row(item: ResolvedMethod) -> dict[str, str]:
        return {
            "qualified_name": item.qualified_name,
            "method_name": item.method_name,
            "class_qualified_name": item.class_qualified_name,
            "service_name": item.service_name,
            "graph_version": item.graph_version,
        }

    def _entities(
        self,
        observation_id: str,
        rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[KeyEntity, ...]:
        entities: list[KeyEntity] = []
        for index, row in enumerate(rows[: self._max_key_entities], start=1):
            attributes = {
                str(key): self._safe_value(value)
                for key, value in row.items()
                if self._is_safe_column(key)
            }
            if not attributes:
                attributes = {"row_index": index}
            entities.append(
                KeyEntity(
                    entity_id=f"{observation_id}:e{index}",
                    kind=self._entity_kind(attributes),
                    attributes=attributes,
                )
            )
        return tuple(entities)

    def _safe_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return value[: self._max_value_chars]
        if value is None or type(value) in {int, float, bool}:
            return value
        if isinstance(value, Mapping):
            return {
                str(key): self._safe_value(item)
                for key, item in list(value.items())[: self._max_collection_items]
            }
        if isinstance(value, (list, tuple)):
            return tuple(
                self._safe_value(item)
                for item in value[: self._max_collection_items]
            )
        return str(value)[: self._max_value_chars]

    @staticmethod
    def _is_safe_column(name: object) -> bool:
        normalized = str(name).strip().lower()
        return normalized not in _SENSITIVE_COLUMNS

    @staticmethod
    def _entity_kind(attributes: Mapping[str, Any]) -> str:
        names = {name.lower() for name in attributes}
        if "qualified_name" in names or "全限定名" in names:
            return "method"
        if {"service_name", "服务名称", "服务"}.intersection(names):
            return "service"
        if {"api_path", "api路径", "上游api路径", "目标api路径"}.intersection(
            names
        ):
            return "api"
        if {"message_queue", "消息队列", "message_exchange", "消息交换机"}.intersection(
            names
        ):
            return "messaging"
        if {"method_path", "方法路径"}.intersection(names):
            return "path"
        return "result_record"

    @staticmethod
    def _result_summary(
        tool: RuntimeToolName,
        row_count: int,
        columns: tuple[str, ...],
        truncated: bool,
    ) -> str:
        if row_count == 0:
            return f"{tool.value}未返回匹配数据。"
        column_text = "、".join(columns[:6]) if columns else "未声明列"
        suffix = "结果已截断。" if truncated else ""
        return f"{tool.value}返回{row_count}条记录；列为{column_text}。{suffix}"

    def _evidence(
        self,
        entities: tuple[KeyEntity, ...],
        rows: tuple[Mapping[str, Any], ...],
        action_fingerprint: str,
    ) -> tuple[str, ...]:
        if not rows:
            return (self._fingerprint({"empty": action_fingerprint}),)
        fingerprints = tuple(
            self._fingerprint(entity.attributes) for entity in entities
        )
        return tuple(dict.fromkeys(fingerprints))

    @staticmethod
    def _fingerprint(value: object) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
