# 方法调用链三查询优化方案

记录日期：2026-09-07。实现基准：`coding-agent-refactor`。

## 一、修正结论

“某方法/API 的调用链”“完整调用链”“端到端调用链”仍由
`full_method_call_chain` 识别原始意图，但不再把服务内、REST、MQ 拼成一条长
Cypher。Primary Agent 必须固定规划为三个可独立执行的查询：

1. 服务内方法调用明细；
2. REST 跨服务调用明细；
3. MQ 发布消费明细。

三个查询均使用 `query_shape=general`，重复原始方法或 API 锚点以及 HTTP 方法、
服务名、图谱版本等显式限定。它们不能使用“上述方法”“前一步结果”等指代，也不消费
其他查询的结果。现有 `SingleRoundRuntime` 按 local、REST、MQ 顺序执行，公开响应保持
`sub_queries` 结构，并满足：

```text
decomposed=true
sub_query_count=3
```

显式“上游调用链”仍使用 `full_entry_chain`；“完整下游调用链”仍使用
`full_downstream_chain`。这两类有方向路径以及两方法有序路径、完整消息路径继续保持为
单个原子查询。

## 二、拆分边界

用户提供的长 Cypher 只作为三类查询的业务语义参考，不作为一次生成的结构模板。

| 子查询 | 独立计算范围 | 固定预算 |
|---|---|---:|
| local | 从原始锚点重新匹配入口或根方法，返回根服务内的有序方法序列 | 10 跳 |
| REST | 从原始锚点重新匹配本地方法序列，再展开 REST 出口、目标服务、目标 API 和入口方法 | 2 层 REST |
| MQ | 从原始锚点重新匹配发布方法，展开交换机、队列、消费者及消费者后续方法序列 | 1 层 MQ；消费者路径 10 跳 |

拆分发生在 Primary Agent/Decomposer 的自然语言规划阶段。每个子查询随后独立进入
`query_code_graph → Graph Query Engine → Parser/Validator/Executor`，每次只生成和
执行一条只读 Cypher。本轮不增加 Runtime、DAG、结果绑定、迭代执行或目录结构。

## 三、锚点与图谱约束

锚点识别顺序为：

1. HTTP 方法、API 路径和服务名组合；
2. 单独 API 路径；
3. 方法全限定名；
4. `类名.方法名`；
5. 单独方法名。

同名方法允许产生多个根结果，每行必须以根方法、源服务和图谱版本区分。API 路径中的
`/v1/` 不是图谱版本；只有用户明确写出“图谱版本 v1”时才作为版本限定。

三条 Cypher 均遵守以下规则：

- 以根节点的 `图谱版本` 绑定本次查询版本，路径中的节点和关系保持同一版本；
- 使用 Schema 声明的关系方向；
- 有序方法序列只使用 `接口调用|链中下一节点`；
- `接口调用` 最多一次且只能位于首跳；
- `链中下一节点` 的 `路径签名` 相同，`位置索引` 连续；
- MQ 必须满足 `发布至.路由键 = 路由至.路由键`；
- REST 出口已经匹配但目标映射缺失时，保留出口行并将映射字段置为 `null`；
- 只投影标量和列表，不返回 Node 或 Path 对象；
- 不生成注释、分号、写操作或过程调用。

## 四、三个表格契约

local 查询返回：

```text
根方法、图谱版本、源服务、源API路径、源HTTP方法、方法路径、叶子方法
```

REST 查询返回：

```text
根方法、图谱版本、REST层级、源服务、源API路径、源HTTP方法、源方法、方法路径、
下游API路径、目标服务、目标API路径、目标HTTP方法、目标方法
```

其中 `REST层级` 只取 1 或 2。第一次 REST 边界的目标服务、目标 API 或入口方法缺失
时，已经确认的出口行仍须返回；只有依赖目标入口方法的第二层结果不产生。

MQ 查询返回：

```text
根方法、图谱版本、源服务、源API路径、源HTTP方法、发布方法、发布方法路径、
消息交换机、消息队列、路由键、目标服务、消费方法、消费者方法路径
```

每个 MQ 行同时携带根服务内到发布方法的路径和消费者后续方法路径，避免发布关系与
消费者路径失去对应。某一类关系不存在时，该子查询可以合法返回空表。

## 五、模块归属

| 模块 | 调整后的职责 |
|---|---|
| QueryShape resolver | 将无方向完整调用链识别为 `full_method_call_chain` 原始意图 |
| Primary Agent Prompt | 明确固定三查询顺序、相同锚点、显式限定和完整字段 |
| Primary 计划校验 | 只接受三个显式 `general` 查询；校验顺序、锚点、限定和字段 |
| Primary fallback | 模型失败或计划非法时生成相同的 local/REST/MQ 三查询 |
| GraphQuerySkillPolicy | 按 intent 和 required information 选择归属、方法调用、入口 API、有序路径及 REST/MQ 规则 |
| Prompt Builder | 不向自然语言完整调用链注入六分支长模板 |
| Graph Query Engine | 不再为自然语言完整调用链自动调用 `CallChainCypherCompiler` |
| Runtime | 复用现有顺序执行和 `sub_queries` 响应，不增加结果绑定 |
| Parser/Validator/Executor | 继续独立校验和执行每条只读 Cypher |
| `CallChainCypherCompiler` / `FindCallChainTool` | 暂时保留为直接调用的兼容能力，不进入 CLI 自然语言调用链路径 |

## 六、测试与评测契约

离线测试必须覆盖：

- 完整调用链 Prompt 要求正好三个 `general` 查询；
- 合法三查询计划可通过，数量、顺序、锚点、限定或字段错误会被拒绝；
- LLM 返回无效 JSON 时 fallback 仍产生相同三查询；
- Runtime 按 q1/local、q2/REST、q3/MQ 顺序执行；
- Graph Query Engine 对完整调用链及其 `general` 子查询走 Translator，不重组长模板；
- 三类子查询选择各自需要的 Skill 规则；
- 三类 evaluation Oracle 使用各自表格列，并允许合法空 `row_set`；
- 既有 `full_entry_chain`、`full_downstream_chain`、有序方法路径和完整消息路径语义不变。

evaluation v6 的四个完整方法调用链案例改为 `must_split`。加载后每个案例包含
`local_call_chain`、`rest_call_chain`、`mq_call_chain` 三个独立 Oracle；旧冻结快照仅作为
迁移输入，评测比较使用拆分后的表格投影。调用链准确率门槛仍为 100%，原有案例不得
出现案例级退化。

## 七、手工验收

先执行离线检查：

```powershell
pytest
ruff check .
mypy
```

连接真实 Neo4j 和模型后执行：

```powershell
text2cypher ask "查询 /api/v1/travelservice/trips/left 的调用链" --json
text2cypher ask "InsidePaymentServiceImpl.pay 的完整调用链是什么？" --json
text2cypher ask "preserve.mq.RabbitSend.send 的调用链是什么？" --json
text2cypher ask "PollThread.doPreserve 的完整调用链是什么，并保留未映射的 REST 出口？" --json
```

每次输出应满足：

- `decomposed=true` 且 `sub_query_count=3`；
- `sub_queries` 顺序为服务内、REST、MQ；
- 每个子查询只包含一条只读 Cypher；
- 三个结果集分别采用本文定义的稳定列；
- REST 不超过两层，MQ 不超过一层；
- MQ 路由键对应，所有路径保持同一图谱版本；
- 没有匹配关系的分支可以返回空表；
- 任一结果 `truncated=true` 时，最终总结不得宣称调用链完整。

真实数据库验收应额外对三类 Oracle 和模型生成语句执行 `EXPLAIN`，并运行三轮 v6
evaluation。该外部验收不由离线测试结果替代。
