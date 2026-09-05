"""受信任的图谱 Schema 获取 Tool。"""

from __future__ import annotations

from text2cypher.domain.models import GraphSchema
from text2cypher.domain.ports import SchemaFetcher


class GetSchemaTool:
    """将当前运行的 SchemaFetcher 暴露为 Runtime 的固定能力。"""

    def __init__(self, schema_fetcher: SchemaFetcher) -> None:
        self._schema_fetcher = schema_fetcher

    def get_schema(self) -> GraphSchema:
        """获取一次实时 Schema；不接受模型生成的数据库语句。"""

        return self._schema_fetcher.fetch()
