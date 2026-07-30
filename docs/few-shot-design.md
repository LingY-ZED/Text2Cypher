# Text2Cypher Few-shot 设计

## 目标

Few-shot 用于向模型提供与当前问题相近、且与实时 Neo4j Schema 完全兼容的查询结构。
动态 `GraphSchema` 和 `SchemaGraph` 始终是标签、属性、关系和方向的最高事实来源。

```text
Neo4jSchemaFetcher
→ GraphSchema
→ SchemaGraphBuilder
→ Schema 兼容过滤
→ 本地混合相似度选择
→ DefaultPromptBuilder
→ LLM
```

首版使用 18 条中文黄金示例，最多注入 3 条。没有合格示例时保留当前 Zero-shot
行为，不增加 embedding、向量库或额外模型调用。

## 分层

- `domain`：`FewShotExample`、`FewShotSchemaRequirements` 和
  `FewShotSelector` 契约。
- `components`：纯逻辑的 Schema 兼容过滤、TF-IDF 和混合排序。
- `infrastructure`：从包资源或外部路径加载 JSON 示例库。
- `application`：根据配置装配 loader、selector 和 PromptBuilder。

PromptBuilder 不读取文件、不查询 Neo4j，也不调用模型。

## JSON 数据格式

```json
{
  "id": "call-downstream-service",
  "category": "call_path",
  "question": "getAllFood 方法调用了哪些下游服务？",
  "cypher": "MATCH ...",
  "aliases": ["查询某方法依赖的下游服务"],
  "tags": ["调用", "下游", "方法", "服务"],
  "schema_requirements": {
    "node_labels": ["方法", "API端点"],
    "relationship_types": ["调用"],
    "node_properties": {
      "方法": ["方法名"],
      "API端点": ["API类型", "目标微服务"]
    },
    "relationship_properties": {
      "调用": ["调用类型"]
    },
    "patterns": [
      {
        "start_labels": ["方法"],
        "relationship_type": "调用",
        "end_labels": ["API端点"]
      }
    ]
  }
}
```

要求：

- `id` 唯一且稳定。
- 问题、Cypher、category 均非空。
- aliases 至少一条。
- 所有 requirement 均显式声明，不能依赖从 Cypher 猜测。
- 示例禁止写入、管理、过程调用、注释和多语句。

## Schema 兼容

选择前按以下顺序过滤：

1. required node labels 是当前节点标签的子集。
2. required relationship types 是当前关系类型的子集。
3. 节点属性必须属于 requirements 指定的节点标签。
4. 关系属性必须属于 requirements 指定的关系类型。
5. required patterns 必须与 SchemaGraphEdge 完全相等。

方向相反、端点不同、缺少属性或只有压缩路径均判定为不兼容。多标签端点保留完整
label tuple，不拆分成虚拟边。

## 混合相似度

文本特征包括中文单字/双字 n-gram、ASCII token、aliases 和 tags。意图识别覆盖：

- `simple`
- `filter`
- `ownership`
- `call`
- `impact`
- `mq`
- `aggregate`
- `compound`

实体形态包括限定方法名、方法标识符、`*-service`、API 路径、MQ 和 REST。

```text
score =
    0.70 × TF-IDF cosine
  + 0.20 × intent overlap
  + 0.10 × entity-shape overlap
```

默认阈值为 0.18，最多选择 3 条，总示例文本不超过 3500 字符。分数相同时按示例
ID 升序，保证结果确定。低于阈值或无兼容示例时回退 Zero-shot。

## Prompt 格式

Few-shot 位于业务语义之后、用户问题之前：

```text
参考示例：
以下示例只用于学习查询结构。
必须使用当前问题中的实体值；
若示例与当前 Schema 冲突，以当前 Schema 和关系方向为准。

示例 1：
问题：...
Cypher：
...

用户问题：
...
```

System Prompt 同时约束：示例不能覆盖 Schema，不得复制示例实体值，所有多跳关系必须
遵守当前 relationship patterns。

## 示例目录

首版固定 18 条：

| 分类 | 数量 | 覆盖 |
| --- | ---: | --- |
| 简单查询、属性过滤 | 3 | 服务列表、类型过滤、方法定位 |
| 归属和 API | 2 | 服务 API、API 所属服务 |
| 调用链、下游服务 | 3 | 方法调用、远程服务、REST 调用 |
| 反向影响 | 3 | 上游服务、上游方法、入口 API |
| MQ | 3 | 服务消息、发布者、完整消息链 |
| 聚合统计 | 2 | REST 服务对、下游调用次数 |
| 复合分析 | 2 | 方法上下游影响、REST+MQ 依赖 |

PDF 中的示例可能与实时关系方向不一致，不能直接复制。示例必须使用当前 SchemaGraph
重新编写，并在提交前逐条执行 Neo4j EXPLAIN。

## 添加示例

1. 在 JSON 中增加唯一 ID、问题、Cypher、aliases、tags 和完整 requirements。
2. 用 SchemaGraph 核对每条边的方向和端点。
3. 增加 selector 排名测试，确保至少一个自然语言改写能选中该示例。
4. 增加 JSON loader 测试或更新示例数量断言。
5. 对新增 Cypher 运行真实 Neo4j EXPLAIN。
6. 运行 pytest、Ruff 和 strict mypy。

## 配置与回滚

```dotenv
TEXT2CYPHER_FEW_SHOT_ENABLED=true
TEXT2CYPHER_FEW_SHOT_TOP_K=3
TEXT2CYPHER_FEW_SHOT_MIN_SCORE=0.18
TEXT2CYPHER_FEW_SHOT_MAX_CHARS=3500
TEXT2CYPHER_FEW_SHOT_LIBRARY_PATH=
```

设置 `TEXT2CYPHER_FEW_SHOT_ENABLED=false` 即可恢复 Zero-shot，无需修改 pipeline 或
PromptBuilder 接口。DeepSeek V4 Flash 的真实验收建议同时设置
`TEXT2CYPHER_LLM_DISABLE_THINKING=true`。

## 非目标

首版不实现 embedding、向量数据库、额外 LLM 选择、问题分解、多查询执行、结果合并、
自修复、示例自动生成和在线学习。
