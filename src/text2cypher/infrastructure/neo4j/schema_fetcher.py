"""基础设施层的 Neo4j 动态 Schema 获取及名称规范化实现。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from time import sleep
from typing import Any

from neo4j import Driver, Query, RoutingControl
from neo4j.exceptions import DriverError, Neo4jError

from text2cypher.components.retry import RetryExecutor, RetryPolicy
from text2cypher.domain.errors import SchemaFetchError
from text2cypher.domain.models import (
    GraphSchema,
    NodeSchema,
    PropertySchema,
    RelationshipPattern,
    RelationshipSchema,
)
from text2cypher.infrastructure.neo4j.retry import run_with_neo4j_retry


class Neo4jSchemaFetcher:
    """使用 Neo4j 内置过程动态获取并结构化图谱 Schema。"""

    _labels_query = "CALL db.labels() YIELD label RETURN label"
    _node_properties_query = (
        "CALL db.schema.nodeTypeProperties() "
        "YIELD nodeLabels, propertyName, propertyTypes, mandatory "
        "RETURN nodeLabels, propertyName, propertyTypes, mandatory"
    )
    _relationship_properties_query = (
        "CALL db.schema.relTypeProperties() "
        "YIELD relType, propertyName, propertyTypes, mandatory "
        "RETURN relType, propertyName, propertyTypes, mandatory"
    )
    _visualization_query = (
        "CALL db.schema.visualization() "
        "YIELD nodes, relationships RETURN nodes, relationships"
    )
    _pattern_fallback_query = (
        "MATCH (start_node)-[relationship]->(end_node) "
        "RETURN DISTINCT labels(start_node) AS start_labels, "
        "type(relationship) AS relationship_type, "
        "labels(end_node) AS end_labels"
    )

    def __init__(
        self,
        driver: Driver,
        database: str,
        timeout_seconds: int,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep_func: Callable[[float], None] = sleep,
    ) -> None:
        self._driver = driver
        self._database = database
        self._timeout_seconds = float(timeout_seconds)
        self._retry_executor = RetryExecutor(
            retry_policy or RetryPolicy(),
            sleep=sleep_func,
        )

    def fetch(self) -> GraphSchema:
        """获取一次实时 Schema；关系模式过程失败时使用安全降级查询。"""

        try:
            labels = self._fetch_labels()
            node_properties = self._fetch_node_properties()
            relationship_properties = self._fetch_relationship_properties()
            try:
                visualization_patterns = self._fetch_visualization_patterns()
            except (DriverError, Neo4jError):
                patterns = self._fetch_fallback_patterns()
            else:
                try:
                    observed_patterns = self._fetch_fallback_patterns()
                except (DriverError, Neo4jError):
                    patterns = visualization_patterns
                else:
                    patterns = observed_patterns or visualization_patterns
            return self._build_schema(
                labels,
                node_properties,
                relationship_properties,
                patterns,
            )
        except (DriverError, Neo4jError, KeyError, TypeError, ValueError):
            raise SchemaFetchError("无法从 Neo4j 获取图谱 Schema") from None

    def _execute(self, cypher: str) -> list[Any]:
        result = run_with_neo4j_retry(
            self._retry_executor,
            lambda: self._driver.execute_query(
                Query(cypher, timeout=self._timeout_seconds),
                routing_=RoutingControl.READ,
                database_=self._database,
            ),
        )
        if hasattr(result, "records"):
            return list(result.records)
        return list(result[0])

    def _fetch_labels(self) -> set[str]:
        labels = set()
        for record in self._execute(self._labels_query):
            labels.add(self._normalize_name(record["label"]))
        return labels

    def _fetch_node_properties(
        self,
    ) -> dict[str, dict[str, tuple[set[str], bool]]]:
        properties: dict[str, dict[str, tuple[set[str], bool]]] = defaultdict(dict)
        for record in self._execute(self._node_properties_query):
            raw_labels = record["nodeLabels"] or []
            property_name = self._normalize_name(record["propertyName"])
            property_types = self._normalize_types(record["propertyTypes"])
            mandatory = bool(record["mandatory"])
            for raw_label in raw_labels:
                label = self._normalize_name(raw_label)
                self._merge_property(
                    properties[label],
                    property_name,
                    property_types,
                    mandatory,
                )
        return properties

    def _fetch_relationship_properties(
        self,
    ) -> dict[str, dict[str, tuple[set[str], bool]]]:
        properties: dict[str, dict[str, tuple[set[str], bool]]] = defaultdict(dict)
        for record in self._execute(self._relationship_properties_query):
            relationship_type = self._normalize_name(record["relType"])
            property_name = self._normalize_name(record["propertyName"])
            property_types = self._normalize_types(record["propertyTypes"])
            mandatory = bool(record["mandatory"])
            self._merge_property(
                properties[relationship_type],
                property_name,
                property_types,
                mandatory,
            )
        return properties

    def _fetch_visualization_patterns(
        self,
    ) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
        records = self._execute(self._visualization_query)
        patterns = set()
        for record in records:
            for relationship in record["relationships"] or []:
                start_labels = self._schema_node_labels(relationship.start_node)
                end_labels = self._schema_node_labels(relationship.end_node)
                relationship_type = self._normalize_name(relationship.type)
                patterns.add((start_labels, relationship_type, end_labels))
        return patterns

    def _fetch_fallback_patterns(
        self,
    ) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
        patterns = set()
        for record in self._execute(self._pattern_fallback_query):
            start_labels = self._normalize_names(record["start_labels"] or [])
            end_labels = self._normalize_names(record["end_labels"] or [])
            relationship_type = self._normalize_name(record["relationship_type"])
            patterns.add((start_labels, relationship_type, end_labels))
        return patterns

    @staticmethod
    def _merge_property(
        properties: dict[str, tuple[set[str], bool]],
        name: str,
        types: set[str],
        mandatory: bool,
    ) -> None:
        existing_types, existing_mandatory = properties.get(name, (set(), False))
        properties[name] = (existing_types | types, existing_mandatory or mandatory)

    @classmethod
    def _build_schema(
        cls,
        labels: set[str],
        node_properties: dict[str, dict[str, tuple[set[str], bool]]],
        relationship_properties: dict[str, dict[str, tuple[set[str], bool]]],
        patterns: set[tuple[tuple[str, ...], str, tuple[str, ...]]],
    ) -> GraphSchema:
        node_names = labels | set(node_properties)
        nodes = tuple(
            NodeSchema(
                name=name,
                properties=cls._to_properties(node_properties.get(name, {})),
            )
            for name in sorted(node_names)
        )
        relationship_names = set(relationship_properties) | {
            relationship_type
            for _, relationship_type, _ in patterns
        }
        relationships = tuple(
            RelationshipSchema(
                name=name,
                properties=cls._to_properties(relationship_properties.get(name, {})),
            )
            for name in sorted(relationship_names)
        )
        relationship_patterns = tuple(
            RelationshipPattern(
                start_labels=start_labels,
                relationship_type=relationship_type,
                end_labels=end_labels,
            )
            for start_labels, relationship_type, end_labels in sorted(patterns)
        )
        return GraphSchema(
            nodes=nodes,
            relationships=relationships,
            patterns=relationship_patterns,
        )

    @staticmethod
    def _to_properties(
        properties: dict[str, tuple[set[str], bool]],
    ) -> tuple[PropertySchema, ...]:
        return tuple(
            PropertySchema(
                name=name,
                types=tuple(sorted(types)),
                mandatory=mandatory,
            )
            for name, (types, mandatory) in sorted(properties.items())
        )

    @classmethod
    def _schema_node_labels(cls, node: Any) -> tuple[str, ...]:
        name = node.get("name") if hasattr(node, "get") else None
        if name is not None:
            return (cls._normalize_name(name),)
        labels = getattr(node, "labels", ())
        return cls._normalize_names(labels)

    @staticmethod
    def _normalize_name(value: Any) -> str:
        normalized = str(value).strip()
        while normalized.startswith(":"):
            normalized = normalized[1:].strip()
        fence = chr(96)
        if normalized.startswith(fence) and normalized.endswith(fence):
            normalized = normalized[1:-1].strip()
        if not normalized:
            raise ValueError("Schema 名称不能为空")
        return normalized

    @classmethod
    def _normalize_names(cls, values: Iterable[Any]) -> tuple[str, ...]:
        return tuple(sorted({cls._normalize_name(value) for value in values}))

    @classmethod
    def _normalize_types(cls, values: Any) -> set[str]:
        if values is None:
            return set()
        if isinstance(values, str):
            return {values.strip()} if values.strip() else set()
        if isinstance(values, Sequence):
            return {str(value).strip() for value in values if str(value).strip()}
        normalized = str(values).strip()
        return {normalized} if normalized else set()
