# Phase 03 / P2：Few-shot 示例库与动态选择

## 总体目标

在现有动态 Schema Prompt 基础上增加：

```text
GraphSchema / SchemaGraph
→ Schema 兼容示例过滤
→ 本地混合相似度排序
→ 选择最多 3 条 Few-shot
→ PromptBuilder
→ LLM
```

首版提供 18 条中文黄金示例，生产默认启用，可配置回退 Zero-shot。不引入
embedding、额外 LLM 调用、问题分解或第三方检索依赖。

## 阶段 0：设计文档与契约冻结

- 创建 `docs/few-shot-design.md`，记录分层架构、数据格式、Schema 兼容规则、
  评分公式、Prompt 模板、示例目录、扩展方法和回滚方式。
- PDF 中的 Cypher 仅用于场景参考，所有关系方向以实时 patterns 为准。
- 本阶段不包含 QuestionDecomposer、embedding 和错误自修复。

## 阶段 1：领域模型、配置与示例加载

新增不可变类型：

```python
FewShotExample(
    id: str,
    category: str,
    question: str,
    cypher: str,
    aliases: tuple[str, ...],
    tags: tuple[str, ...],
    schema_requirements: FewShotSchemaRequirements,
)

FewShotSchemaRequirements(
    node_labels: tuple[str, ...],
    relationship_types: tuple[str, ...],
    node_properties: Mapping[str, tuple[str, ...]],
    relationship_properties: Mapping[str, tuple[str, ...]],
    patterns: tuple[RelationshipPattern, ...],
)
```

新增 `FewShotSelector.select(question, schema, schema_graph)` 协议。现有
`PromptBuilder.build(schema, question)` 和 pipeline 接口保持不变。

默认示例库位于包内 `resources/few_shot_examples.json`，基础设施层通过
`importlib.resources` 加载，并支持可选外部 JSON 路径。

新增配置：

```dotenv
TEXT2CYPHER_FEW_SHOT_ENABLED=true
TEXT2CYPHER_FEW_SHOT_TOP_K=3
TEXT2CYPHER_FEW_SHOT_MIN_SCORE=0.18
TEXT2CYPHER_FEW_SHOT_MAX_CHARS=3500
TEXT2CYPHER_FEW_SHOT_LIBRARY_PATH=
```

`TOP_K` 限制为 1–3，`MIN_SCORE` 限制为 0–1，`MAX_CHARS` 必须为正数。
启用状态下遇到无效示例库应启动失败，禁用时不加载示例。

## 阶段 2：Schema 兼容过滤

示例进入评分前必须满足：

- 所需节点标签和关系类型存在。
- 节点、关系属性属于 requirements 指定的正确类型。
- required pattern 与 SchemaGraph 的起点 labels、relationship type、终点 labels
  完全一致。
- 不通过反转边、传递闭包或压缩路径判定兼容。
- 多标签 pattern 按完整 label tuple 比较。

不兼容示例不参与评分。没有兼容示例时自动回退 Zero-shot。

## 阶段 3：本地混合相似度选择

特征包括：

- 中文单字和双字 n-gram。
- 小写化 ASCII 标识符。
- `Class.method`、方法名、`*-service`、API 路径等实体形态。
- MQ、REST、统计、上下游、影响等意图标签。

评分公式：

```text
score =
    0.70 × TF-IDF cosine
  + 0.20 × intent overlap
  + 0.10 × entity-shape overlap
```

先过滤 Schema，再按分数降序、示例 ID 升序稳定排序。只选择分数不低于
`MIN_SCORE` 的示例，最多选择 `TOP_K` 条，总文本不超过 `MAX_CHARS`。
不为凑数量注入低相关示例。

## 阶段 4：18 条黄金示例库

- 简单查询、属性过滤：3 条。
- 归属和 API 查询：2 条。
- 调用链、下游服务：3 条。
- 反向影响分析：3 条。
- MQ 发布、消费、服务间通信：3 条。
- REST/MQ 聚合统计：2 条。
- 单 Cypher 复合影响或依赖分析：2 条。

复合示例包含：

```text
修改 FoodServiceImpl.getAllFood 会影响哪些上游调用方和下游服务？
```

所有示例使用中文真实 Schema、当前关系方向和已确认业务值。示例实体值只用于演示
结构；禁止写入、管理、过程调用、注释和多语句。每条示例必须包含完整 requirements
和至少一个 alias，并通过真实 Neo4j EXPLAIN。

## 阶段 5：PromptBuilder 与生产装配

Prompt 顺序：

```text
图谱 Schema
→ 可适用的业务语义
→ 参考示例
→ 用户问题
→ 只输出 Cypher
```

示例只用于学习结构，不能覆盖当前 Schema 或复制示例实体值。生产装配在
`FEW_SHOT_ENABLED=true` 时加载示例并构造 selector；关闭时恢复 Zero-shot。
PromptBuilder 不执行文件、Neo4j 或模型 I/O。

## 阶段 6：测试、验收与交付

单元测试覆盖领域模型、JSON 加载、Schema 兼容、混合评分、阈值、Top-K、字符预算、
稳定排序、Zero-shot fallback、Prompt 注入和配置边界。

集成与真实验收：

- 全部 18 条黄金 Cypher 通过 Neo4j EXPLAIN。
- 当前五类验收在 Few-shot 启用时同轮 5/5 通过。
- 禁用 Few-shot 后保持当前 Zero-shot 基线。
- 复合影响问题生成一条同时表达上游和下游的可执行 Cypher。
- DeepSeek V4 Flash 真实验收使用关闭思考模式。

质量门：

```powershell
pytest
ruff check .
mypy

$env:TEXT2CYPHER_RUN_INTEGRATION = "1"
pytest tests\integration\test_neo4j_readonly.py

$env:TEXT2CYPHER_RUN_ACCEPTANCE = "1"
$env:TEXT2CYPHER_LLM_DISABLE_THINKING = "true"
pytest tests\integration\test_text2cypher_acceptance.py
```

当前基线为 `43 passed, 6 skipped`，Ruff 和 strict mypy 均通过。

## 阶段边界

本次不实现 embedding、向量数据库、额外 LLM 选择调用、QuestionDecomposer、
多 Cypher 执行、结果合并、Self-Correction、示例自动生成或在线学习。
