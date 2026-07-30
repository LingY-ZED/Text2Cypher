"""Schema 兼容且确定性的本地 Few-shot 混合选择器。"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from text2cypher.components.few_shot_schema_filter import (
    FewShotSchemaCompatibilityFilter,
)
from text2cypher.domain.models import (
    FewShotExample,
    GraphSchema,
    SchemaGraph,
)

_ASCII_TOKEN = re.compile(r"[a-z0-9_$]+(?:[./:-][a-z0-9_$]+)*")
_CHINESE_RUN = re.compile(r"[\u3400-\u9fff]+")
_QUALIFIED_IDENTIFIER = re.compile(
    r"\b[a-z_$][a-z0-9_$]*\.[a-z_$][a-z0-9_$]*\b"
)
_METHOD_IDENTIFIER = re.compile(
    r"\b(?:[a-z_$][A-Za-z0-9_$]*[A-Z][A-Za-z0-9_$]*"
    r"|[a-z_$][a-z0-9_$]*_[a-z0-9_$]+)\b"
)
_SERVICE_NAME = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+)*-service\b")
_API_PATH = re.compile(r"/[a-z0-9_{}*?./:-]+")

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "simple": ("列出", "所有", "有哪些", "查看"),
    "filter": ("查找", "类型", "名称为", "等于", "筛选"),
    "ownership": ("属于", "所属", "提供", "归属", "端点", "接口"),
    "call": ("调用", "调用链", "下游", "依赖"),
    "impact": ("影响", "上游", "调用方", "入口", "反向"),
    "mq": ("mq", "消息", "队列", "交换机", "发布", "消费", "路由"),
    "aggregate": ("统计", "数量", "次数", "多少", "count", "最多"),
}


@dataclass(frozen=True, slots=True)
class _ScoredExample:
    example: FewShotExample
    score: float


class HybridFewShotSelector:
    """以 Schema 过滤、TF-IDF、意图和实体形态选择黄金示例。"""

    def __init__(
        self,
        examples: tuple[FewShotExample, ...],
        *,
        top_k: int = 3,
        min_score: float = 0.18,
        max_chars: int = 3500,
        compatibility_filter: FewShotSchemaCompatibilityFilter | None = None,
    ) -> None:
        if not 1 <= top_k <= 3:
            raise ValueError("top_k 必须在 1 到 3 之间")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score 必须在 0 到 1 之间")
        if max_chars <= 0:
            raise ValueError("max_chars 必须为正数")
        self._examples = tuple(examples)
        self._top_k = top_k
        self._min_score = min_score
        self._max_chars = max_chars
        self._compatibility_filter = (
            compatibility_filter or FewShotSchemaCompatibilityFilter()
        )

    def select(
        self,
        question: str,
        schema: GraphSchema,
        schema_graph: SchemaGraph,
    ) -> tuple[FewShotExample, ...]:
        normalized_question = question.strip()
        if not normalized_question:
            return ()

        compatible = tuple(
            example
            for example in self._examples
            if self._compatibility_filter.is_compatible(
                example,
                schema,
                schema_graph,
            )
        )
        if not compatible:
            return ()

        question_features = self._text_features(normalized_question)
        example_features = tuple(
            self._text_features(self._selection_text(example))
            for example in compatible
        )
        text_scores = self._tfidf_cosine_scores(
            question_features,
            example_features,
        )
        question_intents = self._intent_features(normalized_question)
        question_shapes = self._entity_shapes(normalized_question)
        scored = []
        for example, text_score in zip(
            compatible,
            text_scores,
            strict=True,
        ):
            example_text = self._selection_text(example)
            intent_score = self._jaccard(
                question_intents,
                self._intent_features(example_text),
            )
            shape_score = self._jaccard(
                question_shapes,
                self._entity_shapes(example_text),
            )
            score = 0.70 * text_score + 0.20 * intent_score + 0.10 * shape_score
            if score >= self._min_score:
                scored.append(_ScoredExample(example=example, score=score))

        ranked = sorted(scored, key=lambda item: (-item.score, item.example.id))
        selected: list[FewShotExample] = []
        used_chars = 0
        for item in ranked:
            if len(selected) >= self._top_k:
                break
            block_size = self._prompt_block_size(item.example, len(selected) + 1)
            if used_chars + block_size > self._max_chars:
                break
            selected.append(item.example)
            used_chars += block_size
        return tuple(selected)

    @classmethod
    def _selection_text(cls, example: FewShotExample) -> str:
        return " ".join(
            (
                example.question,
                *example.aliases,
                *example.tags,
            )
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return unicodedata.normalize("NFKC", text).lower()

    @classmethod
    def _text_features(cls, text: str) -> Counter[str]:
        normalized = cls._normalize(text)
        features: Counter[str] = Counter()
        for token in _ASCII_TOKEN.findall(normalized):
            features[f"ascii:{token}"] += 1
        for run in _CHINESE_RUN.findall(normalized):
            for character in run:
                features[f"zh1:{character}"] += 1
            for index in range(len(run) - 1):
                features[f"zh2:{run[index:index + 2]}"] += 1
        return features

    @staticmethod
    def _tfidf_cosine_scores(
        question: Counter[str],
        examples: tuple[Counter[str], ...],
    ) -> tuple[float, ...]:
        document_count = len(examples)
        document_frequency: Counter[str] = Counter()
        for features in examples:
            document_frequency.update(features.keys())
        inverse_document_frequency = {
            term: math.log(
                (1 + document_count) / (1 + frequency)
            )
            + 1
            for term, frequency in document_frequency.items()
        }
        question_vector = HybridFewShotSelector._tfidf_vector(
            question,
            inverse_document_frequency,
        )
        return tuple(
            HybridFewShotSelector._cosine(
                question_vector,
                HybridFewShotSelector._tfidf_vector(
                    features,
                    inverse_document_frequency,
                ),
            )
            for features in examples
        )

    @staticmethod
    def _tfidf_vector(
        features: Counter[str],
        inverse_document_frequency: dict[str, float],
    ) -> dict[str, float]:
        total = sum(features.values())
        if total == 0:
            return {}
        return {
            term: count / total * inverse_document_frequency[term]
            for term, count in features.items()
            if term in inverse_document_frequency
        }

    @staticmethod
    def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        dot_product = sum(
            value * right.get(term, 0.0) for term, value in left.items()
        )
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot_product / (left_norm * right_norm)

    @classmethod
    def _intent_features(cls, text: str) -> frozenset[str]:
        normalized = cls._normalize(text)
        intents = {
            intent
            for intent, keywords in _INTENT_KEYWORDS.items()
            if any(keyword in normalized for keyword in keywords)
        }
        connectors = ("和", "以及", "同时", "上下游", "综合")
        if len(intents - {"simple", "filter"}) >= 2 and any(
            connector in normalized for connector in connectors
        ):
            intents.add("compound")
        return frozenset(intents)

    @classmethod
    def _entity_shapes(cls, text: str) -> frozenset[str]:
        normalized = cls._normalize(text)
        shapes = set()
        if _QUALIFIED_IDENTIFIER.search(normalized):
            shapes.add("qualified_identifier")
        if _METHOD_IDENTIFIER.search(text):
            shapes.add("method_identifier")
        if _SERVICE_NAME.search(normalized):
            shapes.add("service_name")
        if _API_PATH.search(normalized):
            shapes.add("api_path")
        if "mq" in normalized or "消息" in normalized:
            shapes.add("mq")
        if "rest" in normalized:
            shapes.add("rest")
        if any(keyword in normalized for keyword in ("统计", "次数", "数量", "count")):
            shapes.add("aggregate")
        if "上游" in normalized or "上下游" in normalized:
            shapes.add("upstream")
        if "下游" in normalized:
            shapes.add("downstream")
        return frozenset(shapes)

    @staticmethod
    def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @staticmethod
    def _prompt_block_size(example: FewShotExample, index: int) -> int:
        return len(
            f"示例 {index}：\n问题：{example.question}\n"
            f"Cypher：\n{example.cypher}"
        )
