"""将结构化 Schema 转换为轻量有向图。"""

from __future__ import annotations

from collections import defaultdict

from text2cypher.domain.models import GraphSchema, SchemaGraph, SchemaGraphEdge


class SchemaGraphBuilder:
    """仅根据真实 relationship patterns 构建 Schema 图。"""

    def build(self, schema: GraphSchema) -> SchemaGraph:
        nodes = {node.name for node in schema.nodes}
        edges = {
            SchemaGraphEdge(
                start_labels=pattern.start_labels,
                relationship_type=pattern.relationship_type,
                end_labels=pattern.end_labels,
            )
            for pattern in schema.patterns
        }

        for edge in edges:
            nodes.update(edge.start_labels)
            nodes.update(edge.end_labels)

        sorted_nodes = tuple(sorted(nodes))
        sorted_edges = tuple(sorted(edges))
        outgoing: dict[str, list[SchemaGraphEdge]] = defaultdict(list)
        incoming: dict[str, list[SchemaGraphEdge]] = defaultdict(list)

        for node in sorted_nodes:
            outgoing[node]
            incoming[node]

        for edge in sorted_edges:
            for start_label in edge.start_labels:
                outgoing[start_label].append(edge)
            for end_label in edge.end_labels:
                incoming[end_label].append(edge)

        return SchemaGraph(
            nodes=sorted_nodes,
            edges=sorted_edges,
            outgoing={
                node: tuple(node_edges)
                for node, node_edges in sorted(outgoing.items())
            },
            incoming={
                node: tuple(node_edges)
                for node, node_edges in sorted(incoming.items())
            },
        )
