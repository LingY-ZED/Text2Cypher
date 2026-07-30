"""领域层对外暴露的分阶段异常。"""


class Text2CypherError(RuntimeError):
    """预期的 Text2Cypher 错误基类。"""


class ConfigurationError(Text2CypherError):
    """运行时配置缺失或不合法时抛出。"""


class FewShotLibraryError(ConfigurationError):
    """Few-shot 示例库不存在或内容非法。"""


class QuestionValidationError(Text2CypherError):
    """用户问题为空或不合法时抛出。"""


class SchemaFetchError(Text2CypherError):
    """无法获取图谱 Schema 时抛出。"""


class Neo4jConnectionError(Text2CypherError):
    """无法连接或认证 Neo4j 时抛出。"""


class PromptBuildError(Text2CypherError):
    """无法构造模型提示词时抛出。"""


class LLMGenerationError(Text2CypherError):
    """模型请求或响应不合法时抛出。"""


class CypherParseError(Text2CypherError):
    """无法从模型输出中提取可用 Cypher 时抛出。"""


class CypherValidationError(Text2CypherError):
    """Cypher 不安全或不是只读查询时抛出。"""


class CypherExecutionError(Text2CypherError):
    """Neo4j 无法执行已批准查询时抛出。"""


class BootstrapNotReadyError(Text2CypherError):
    """真实生产适配器尚未完成接线时抛出。"""
