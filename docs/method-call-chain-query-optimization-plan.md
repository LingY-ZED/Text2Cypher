# 方法调用链查询与表格化输出优化计划

记录日期：2026-09-06。分析基准：`coding-agent-refactor@b49631c`。

## 一、结论与实施边界

本文仅保存方法调用链查询优化方案，不修改代码、测试、配置、Prompt 或 evaluation 数据。

当前仓库已有：

- 9 种 `QueryShape`；
- 1 个 `full_downstream_chain` 结构模板；
- 26 条 Few-shot；
- 单轮 `Primary Agent → Runtime → query_code_graph → Graph Query Engine → Core` 链路；
- evaluation v5 共 40 题，其中 10 题属于 `call_chain`，调用链正确率是 100% 硬门槛。

本次优化建议新增独立的 `full_method_call_chain` 查询形状，用于“某方法的调用链”“完整调用链”“端到端调用链”等无上游限定的问题。现有形状保持原语义：

- `full_entry_chain`：从入口到目标方法的上游完整链；
- `full_downstream_chain`：目标方法到第一个 REST 出口的完整下游链；
- `full_method_call_chain`：从指定方法或入口开始，展开服务内方法路径、两层 REST 和一层 MQ 链路。

首期固定预算：

| 维度 | 上限 |
|---|---:|
| 每个服务内方法路径 | 10 跳 |
| REST 跨服务边界 | 2 层 |
| MQ 发布消费边界 | 1 层 |
| MQ 消费者后续服务内路径 | 10 跳 |

## 二、对示例方案的必要调整

用户示例应作为目标语义参考，不应原样复制。

1. 示例包含多个 `MATCH/OPTIONAL MATCH`，但仍是一条 Cypher。当前 Parser 和 Validator 允许这种结构，也允许 `UNION`；只有多个分号分隔的语句才属于多语句。
2. 当前 Decomposer 只能生成彼此独立的自然语言子问题，不能把 `leaf`、`tgtLeaf` 等结果绑定给后续步骤。因此首期不得将本地、REST、MQ 链机械拆成多个子查询。
3. Validator 禁止 Cypher 注释、过程调用和多语句。正式模板不得包含示例中的 `//` 注释、`CALL {}` 或分号。
4. 不硬编码 `图谱版本='v1'`。以锚点节点的 `图谱版本` 为本次查询版本，所有路径节点和关系都与其比较；用户明确指定版本时才增加字面量过滤。
5. 所有关系使用 Schema 中的明确方向：
   - `方法-[:归属于]->类-[:归属于]->微服务`
   - `方法-[:服务于]->上游API`
   - `方法-[:下游调用]->下游API`
   - `下游API-[:目标服务]->微服务`
   - `下游API-[:外部调用]->上游API`
6. 完整有序方法链不混用普通 `调用`。采用 `接口调用|链中下一节点`，其中：
   - `接口调用` 最多一次且只能位于首跳；
   - `链中下一节点` 必须具有相同 `路径签名`；
   - 相邻 `位置索引` 必须连续。
7. MQ 分支必须补充现有业务规则要求的路由键对应：`发布至.路由键 = 路由至.路由键`。
8. 不返回节点或 Path 对象。所有结果投影为标量、列表及稳定列名。
9. 不用一组连续 `OPTIONAL MATCH` 拼接所有分支，避免 REST、MQ 和多个入口映射产生笛卡尔积。采用同一 Cypher 内的多个 `UNION` 分支。

## 三、目标查询语义与表格契约

### 3.1 锚点规则

锚点识别顺序固定为：

1. 方法全限定名：精确匹配；
2. `类名.方法名`：类名后缀加方法名匹配；
3. 单独方法名：返回全部同名方法，并通过“根方法、源服务、图谱版本”显式区分；
4. 问题同时提供服务、HTTP 方法或 API 路径时，将其作为消歧条件；
5. 不根据 Few-shot 示例复制实体值。

显式“上游调用链”仍进入 `full_entry_chain`，不会被新形状覆盖。

### 3.2 统一结果列

每个本地、REST 或 MQ 分段返回一行，所有 `UNION` 分支使用相同列：

| 列名 | 语义 |
|---|---|
| `根方法` | 整条查询的原始方法锚点 |
| `图谱版本` | 从根节点绑定的版本 |
| `层级` | 跨服务深度：根服务为 0，第一次跨服务为 1 |
| `链路类型` | 稳定值 `local`、`rest` 或 `mq` |
| `源服务` | 当前分段所在服务 |
| `源API路径` | 当前服务对应的入口 API，可为空 |
| `源HTTP方法` | 当前入口 API 的 HTTP 方法，可为空 |
| `源方法` | 当前分段起点或跨边界调用方法 |
| `方法路径` | 当前服务内按顺序排列的方法全限定名列表 |
| `下游API路径` | REST 调用的下游 API，可为空 |
| `目标服务` | REST 目标服务或 MQ 消费服务 |
| `目标API路径` | REST 映射到的目标上游 API |
| `目标HTTP方法` | 目标上游 API 的 HTTP 方法 |
| `目标方法` | 本地叶子、REST 入口方法或 MQ 消费方法 |
| `消息交换机` | MQ 分支的交换机 |
| `消息队列` | MQ 分支的队列 |
| `路由键` | 已校验对应关系的 MQ 路由键 |

无关字段返回 `null`。REST/MQ 行继续携带到达其源方法的 `方法路径`，从而保留分支对应关系。

### 3.3 物理查询分支

单条 Cypher 由六类 `UNION` 分支构成：

1. 根服务内方法路径，`层级=0`、`链路类型=local`；
2. 根服务叶子方法的第一次 REST 出口，`层级=1`；
3. 第一个目标入口方法的服务内路径，`层级=1`；
4. 第一个目标服务叶子方法的第二次 REST 出口，`层级=2`；
5. 根服务叶子方法的一次 MQ 发布消费链，`层级=1`；
6. MQ 消费方法所在服务的后续方法路径，`层级=1`。

目标服务、目标上游 API 或入口方法缺失时，第一次 REST 边界行必须保留；只有依赖相应入口方法的后续分支不产生结果。

首期不继续展开第二个 REST 目标服务内部路径，也不从 REST 目标服务继续展开 MQ，不从 MQ 消费者继续跨 REST/MQ。

## 四、现有模块调整方案

| 位置 | 调整 |
|---|---|
| `QueryShape` | 新增 `FULL_METHOD_CALL_CHAIN`，不修改已有枚举值 |
| QueryShape resolver | 将无方向的“某方法调用链、完整调用链、端到端调用链”映射到新形状 |
| Primary Agent 能力说明 | 明确新形状的锚点、范围、返回表格及与上下游链的区别 |
| Primary 计划校验 | 将新形状加入不可拆分的原子路径集合 |
| `call_analysis` Skill | 增加完整方法调用链语义和有序路径约束 |
| `dependency_analysis` Skill | 提供 REST、MQ 两类跨边界规则 |
| `GraphQuerySkillPolicy` | 为新形状选择 core、ownership、method call、entry API、REST、MQ、ordered path |
| QueryShape Template | 新增专用结构模板及完整 Schema requirements |
| Prompt Builder | 继续注入动态 Schema、Primary 计划、Skill 视图和结构模板 |
| Few-shot | 不复制整条超长查询；增加短元数据示例帮助 Router 识别意图，物理结构以模板为准 |
| Graph Query Engine | 首期仍通过现有 Text2Cypher 翻译和安全执行链运行 |
| Runtime/Decomposer | 保持单个 `PrimaryAgentQuery`，不增加结果绑定或跨子查询联结 |
| Formatter/Summarizer | 保留现有公开响应；新查询自然形成稳定 columns/rows |

由于完整模板明显长于普通查询，应将默认模型输出上限从 512 调整为 4096，环境变量仍可覆盖。该改动只扩大允许的最大输出，不改变 Prompt 或响应格式。

## 五、与目标架构的衔接

首期继续由 `query_code_graph` 调用 Graph Query Engine，因为当前只有字符串 `anchor`，尚未具备安全的参数化 `QueryStatement` 和实体解析 Tool。此时直接增加所谓“确定性调用链 Tool”会迫使实现字符串拼接或重复一套实体识别逻辑。

完成参数化执行和 `resolve_symbol` 后，按相同输出契约增加：

```text
find_call_chain(
  anchor,
  graph_version=null,
  local_hops=10,
  rest_hops=2,
  mq_hops=1
) -> CallChainSegment[]
```

届时：

- `CallChainQuerySpec` 成为 QueryShape 的结构化扩展；
- 确定性编译器生成参数化 Cypher；
- Text2Cypher 只负责从自然语言形成查询规格；
- 当前模板成为编译器契约和 LLM 降级参考；
- MCP/API 可直接复用相同 `CallChainSegment` 表格模型。

未来迭代 Runtime 才将逻辑链拆成：

```text
Resolve anchor
→ Expand local path
→ Observe leaves
→ Expand REST/MQ
→ Observe new frontier
→ Replan until budget exhausted
```

每一步必须携带显式 `depends_on`、输入绑定、剩余深度和 visited 集合。该模型落地前，不复用当前 Decomposer 承担结果依赖。

## 六、分阶段实施与回滚

| 阶段 | 实施内容 | 验收标准 | 回滚边界 |
|---|---|---|---|
| C0：冻结语义 | 保存新形状、深度、列名、版本和关系方向契约 | 文档评审通过；既有行为无变化 | 仅文档提交 |
| C1：查询形状与 Skills | 新增枚举、路由、Primary 原子约束和 Skill 选择 | 相关单测通过；已有形状路由全部不变 | 回退形状与知识资源提交 |
| C2：结构模板 | 实现六类 `UNION` 分支、版本一致性和表格投影 | 模板通过资源校验、Schema 兼容过滤及 Neo4j `EXPLAIN` | 回退模板提交；旧形状继续工作 |
| C3：生成与展示 | 调整输出预算，接入现有 Engine/Core，验证 JSON 表格与总结 | 单次规划、单条 Cypher、稳定列名、无节点对象 | 回退接线与配置提交 |
| C4：测试与 evaluation | 增加真实快照、接受测试和三轮评测 | 新旧调用链全部通过，既有 40 题无退化 | evaluation 版本和功能提交可分别回退 |
| C5：确定性迁移 | 参数化语句、实体解析、`find_call_chain` 编译器 | 相同规格产生稳定 Cypher 和相同表格结果 | 独立能力提交；保留 Text2Cypher 回退 |

每个阶段独立提交，不混合目录搬迁、MCP 接入或 Iterative Runtime。

## 七、测试与 evaluation

### 7.1 单元测试

新增或扩展测试覆盖：

- 新查询措辞正确解析为 `FULL_METHOD_CALL_CHAIN`；
- “上游调用链”和“完整下游调用链”保持原 QueryShape；
- Primary Agent 不得把 REST、MQ、表格列拆成多个查询；
- 新形状选择完整的调用、REST、MQ、有序路径 Skill；
- 模板只包含一个语句，不含注释、分号、`CALL` 和写操作；
- 所有 `UNION` 分支列名和顺序完全一致；
- 不出现 `RETURN p0`、`RETURN node` 等节点或 Path 投影；
- 不硬编码 `v1`；
- 所有节点、关系与根锚点版本一致；
- `接口调用` 最多一次且只在首跳；
- `链中下一节点` 的路径签名相同、位置连续；
- MQ 路由键匹配；
- REST 映射缺失时仍保留边界行；
- 方法同名时返回可区分的多根结果；
- 当前 Parser/Validator 仍拒绝真正的多语句和注释。

### 7.2 集成与手工验收

安装开发依赖后执行：

```powershell
pytest
ruff check .
mypy
```

启用 Neo4j 只读集成测试后，逐个对结构模板执行 `EXPLAIN` 和真实查询，并确认：

- `decomposed=false`、`sub_query_count=1`；
- Cypher 中出现本地、两层 REST 和 MQ 分支；
- 返回列符合统一表格契约；
- `链路类型` 仅为 `local/rest/mq`；
- 没有跨图谱版本的节点或关系；
- `truncated=true` 时不宣称结果完整；
- 查询耗时未突破现有 30 秒质量门槛。

CLI 手工问题至少包括：

```powershell
text2cypher ask "travel.service.TravelServiceImpl.getTickets 的调用链是什么？" --json
text2cypher ask "InsidePaymentServiceImpl.pay 的完整调用链是什么？" --json
text2cypher ask "preserve.mq.RabbitSend.send 的调用链是什么？" --json
text2cypher ask "POST /api/v1/travelservice/trips/left 在 ts-travel-service 中的调用链是什么？" --json
```

### 7.3 evaluation

将数据集升级为 v6，新增 4 个 `hard/call_chain/must_preserve` 案例：

1. 用户示例中的 travel-service API 链；
2. `InsidePaymentServiceImpl.pay` 的两层 REST 链；
3. `preserve.mq.RabbitSend.send` 的 MQ 消费链；
4. 同名方法或映射缺失场景。

每个 Oracle 使用统一表格列和 `row_set` 比较，冻结真实 Neo4j 快照。验收要求：

- 新增调用链案例三轮全部语义正确；
- 原有 10 个调用链案例继续全部通过；
- `call_chain_accuracy=100%`；
- decomposition contract 全部通过；
- 原有 40 题不得出现案例级退化；
- 生产与 evaluation 继续使用同一 application assembly。

当前规划阶段已验证相关离线测试 70 项通过。当前 Shell 未安装 `neo4j` 包，因此未声称完整测试和真实数据库验收已通过。
