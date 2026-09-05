# P3：Skills 与 Graph Profile 迁移计划

## 目标与范围

本阶段把当前散落在 `components` 中的图谱领域知识组织为可被 Runtime 和
Graph Query Engine 消费的 Skills 与 Graph Profile，同时保持现有单轮查询的
可观察行为不变。本阶段不引入 MCP、外部 API、实体解析、专项确定性 Tool，
也不改变 Primary Agent 的规划、`QueryShape` 枚举、Cypher 恢复次数或执行策略。

本阶段针对当前实现落实以下边界：

```text
Skills（任务语义、阶段选择、知识视图）
  └─ Graph Profile（当前代码图谱的物理标签、边、方向和资源片段）
       └─ Query Engine / PromptBuilder / Few-shot Router

Deterministic Graph Core（Parser → Validator → Executor）
  └─ 不拥有业务标签、关系方向或中文场景线索
```

`QueryShape` 仍是规划契约中的轻量结构化意图。中文场景线索和 QueryShape 到
规则模块的映射属于 Skill policy；物理关系说明仍属于当前代码图谱 Profile。

## 当前实现与迁移决定

当前 `code_graph_business_rules` 同时保存模块标识、资源读取和物理图说明；
`code_graph_business_rule_selector` 保存中文线索、QueryShape 映射和阶段排除
规则；`code_graph_business_rule_routing` 保存 Router 的精简知识视图；Primary
Agent 与旧 Decomposer 都直接读取独立的 semantic-capabilities 资源。`PromptBuilder`
和 Few-shot Router 从这些 `components` 路径装配 Prompt。

另外，`JsonQueryShapeTemplateLoader` 直接依赖
`infrastructure.few_shot.JsonFewShotExampleLoader` 的静态校验方法。这让高层
查询结构资产反向依赖 Few-shot 基础设施，且把通用 JSON Schema requirements
契约隐藏在某个具体资源加载器中。

本阶段采用以下迁移：

| 原有职责 | P3 归属 | 兼容策略 |
|---|---|---|
| `BusinessRuleModule`、模块资源读取 | `skills.graph_profile` | 旧模块路径重新导出 |
| 中文线索、形状映射、阶段排除 | `skills.policies.GraphQuerySkillPolicy` | 旧 Selector 名称重新导出 |
| Router 精简语义、Translator 渲染 | `skills.views` | 旧 routing 函数重新导出 |
| Primary/Decomposer 抽象能力说明 | `skills.registry` 的 planning view | 旧 loader 重新导出 |
| 五个 Skill 的稳定清单 | `skills.models` 与 `skills.registry` | 新增只读元数据，不改变选择算法 |
| JSON requirements 和只读样例校验 | `domain.resource_contracts` | Few-shot 与模板共同调用 |

不会在 P3 物理移动 `resources/few_shot_examples.json`、
`resources/query_shape_templates.json`、`resources/primary_agent_semantic_capabilities.md`
或 `resources/code_graph_business_rules/*.md`。这些文件是 P0 资源来源记录的一部分，
且 Prompt 兼容要求包括字节级片段顺序。P3 先改变代码归属，资源路径继续作为
当前 `code-graph-v1` Profile 的显式实现细节；后续若需要移动资源，必须以独立提交
更新来源哈希并重放 Prompt 基线。

## Skill 清单

| ID | 覆盖的当前规则模块 | 作用 |
|---|---|---|
| `code_structure` | `core`、`anchor_ownership` | 类、方法、接口实现、归属和属性查询 |
| `call_analysis` | `method_call`、`ordered_path` | 直接/可达调用、有序路径和完整调用链 |
| `api_analysis` | `entry_api` | API 契约、入口和入口路径 |
| `dependency_analysis` | `rest`、`mq`、`aggregation` | REST、MQ、服务依赖和关联聚合 |
| `change_impact` | `decomposition` | 变更题的独立查询视图与拆分约束 |

Skill 清单提供稳定 ID、版本、支持的 QueryShape 和 Profile 模块引用。它不增加新的
前置 LLM 分类：`GraphQuerySkillPolicy.select()` 保留现有确定性规则模块选择，
`select_skills()` 仅将已选模块映射为可审计的 Skill 元数据。当前 Router 和
Translator 继续按模块渲染，因而不会改变 Prompt 文本。

## 兼容契约

1. `BusinessRuleModule` 枚举成员、全局顺序、旧导入路径与资源异常类型不变。
2. 相同问题、显式 QueryShape、示例元数据和 Prompt 阶段得到相同模块集合。
3. Translator 与 Router 仍以相同顺序渲染相同文本；Primary Agent 与旧 Decomposer
   读取同一份抽象能力说明。
4. Few-shot 的 26 个 ID、内容、排序、Schema requirements 和 QueryShape 不变；
   结构模板的读取与 Schema 兼容判定不变。
5. `JsonQueryShapeTemplateLoader` 不再导入 Few-shot 基础设施；两个可信 JSON
   资源通过同一纯数据契约进行 requirements 与只读 Cypher 校验。

## 验证与回滚

新增迁移测试覆盖 Skill 注册表、旧/新知识视图的等价渲染，以及 policy 的稳定模块和
Skill 映射。现有 Business Rules、Prompt、Few-shot、Template、Primary 和
Decomposer 测试继续运行，作为行为基线。执行 Ruff、pytest 和 mypy；真实
Neo4j/LLM 评测不属于本阶段离线结构迁移验收。

本阶段只新增 `skills` 和共享资源契约，并将旧 `components` 路径变为转发层。
回滚时可整体撤销该独立提交，P2 的 Query Engine、Gateway、资源文件、数据库
配置和外部接口都不受影响。
