"""完整方法调用链的确定性、参数化 Cypher 编译器。"""

from __future__ import annotations

from text2cypher.components.query_shape_templates import JsonQueryShapeTemplateLoader
from text2cypher.domain.models import CallChainQuerySpec, QueryStatement
from text2cypher.domain.query_shapes import QueryShape


class CallChainCypherCompiler:
    """从受限规格编译完整调用链，不接收任意 Cypher 文本。"""

    _TEMPLATE_ID = "full-method-call-chain-structure"

    def __init__(
        self,
        template_loader: JsonQueryShapeTemplateLoader | None = None,
    ) -> None:
        self._template_loader = template_loader or JsonQueryShapeTemplateLoader()

    def compile(self, spec: CallChainQuerySpec) -> QueryStatement:
        """根据深度预算选择分支并绑定方法、版本参数。"""

        if not isinstance(spec, CallChainQuerySpec):
            raise TypeError("调用链查询必须是 CallChainQuerySpec")
        template = next(
            item.template
            for item in self._template_loader.load()
            if item.id == self._TEMPLATE_ID
            and item.query_shape is QueryShape.FULL_METHOD_CALL_CHAIN
        )
        branches = template.split("\nUNION\n")
        if len(branches) != 6:
            raise ValueError("完整方法调用链模板必须包含六个物理分支")
        selected = [branches[0]]
        if spec.rest_hops >= 1:
            selected.extend(branches[1:3])
        if spec.rest_hops >= 2:
            selected.append(branches[3])
        if spec.mq_hops == 1:
            selected.extend(branches[4:6])

        predicate = "anchorMethod.全限定名 = $anchorQualifiedName"
        parameters: dict[str, str] = {
            "anchorQualifiedName": spec.anchor_qualified_name,
        }
        if spec.graph_version is not None:
            predicate += " AND graphVersion = $graphVersion"
            parameters["graphVersion"] = spec.graph_version
        cypher = "\nUNION\n".join(selected).replace(
            "*0..10",
            f"*0..{spec.local_hops}",
        )
        cypher = cypher.replace("<TARGET_METHOD_FILTER>", predicate)
        if "<TARGET_METHOD_FILTER>" in cypher:
            raise ValueError("完整方法调用链模板含有未替换的锚点占位符")
        return QueryStatement(cypher=cypher, parameters=parameters)
