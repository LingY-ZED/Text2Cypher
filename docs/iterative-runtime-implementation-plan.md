# CODEXGRAPH 风格 IterativeRuntime 实施计划

## 1. 目标与兼容边界

本阶段在现有单轮查询链路旁新增独立的迭代查询 Runtime：

```text
Question
  -> Plan
  -> Execute typed Tools
  -> Observe
  -> Judge sufficiency / Replan
  -> ...
  -> Answer
```

设计参考 CODEXGRAPH 的两项原则：Primary LLM 只负责高层分析和自然语言查询规划，
查询翻译由专门的 Query Engine 完成；每轮必须基于原问题和此前检索到的信息判断上下文
是否充分，而不是预先生成若干互相独立的查询后一次性结束。

兼容边界如下：

- 保留 `SingleRoundRuntime`、`PrimaryAgentPlan` 和现有 `Text2CypherPipeline` 行为作为
  baseline。
- `IterativeRuntime` 通过新的显式 factory/API 构造；本阶段不切换生产 Pipeline、CLI
  或 evaluation 的默认入口。
- 每次迭代运行只获取一次 Schema，并在所有轮次复用同一个 `QueryContext`。这不表示
  数据库事务快照。
- Skills、Graph Profile、确定性 Tools、Graph Core、只读 Gateway 和现有安全限制全部
  复用，不扩大模型可调用权限。
- Query Engine 中的 Prompt、Parser、Validator、Neo4j `EXPLAIN`、Corrector、空结果
  复核和 Transport Retry 仍是一次 `query_code_graph` Action 内部的 Query Recovery，
  不产生新的 Runtime 轮次或 Action。
- 不实现并行执行、同轮 Action 依赖、持久化恢复、MCP/API、CLI 模式切换或其他无关
  重构。

## 2. 领域契约

### 2.1 Planner 决策

`PlanDecision` 只有两个值：

- `continue`：当前证据不足，必须给出一到三个下一步 Action。
- `complete`：当前证据足以回答，`missing_information` 和 `actions` 必须为空。

`IterativePlan` 包含：

- `decision`
- `obtained_information`
- `missing_information`
- `actions`

当 decision 为 `continue` 时，`missing_information` 必须非空，Action 数必须为 1～3；
当 decision 为 `complete` 时不得再调度 Tool。Planner 第一次在无 Observation 的上下文中
生成第一批 Action；每个执行轮次结束后再次调用 Planner，完成充分性判断并在需要时生成
下一轮 Action。

### 2.2 Action 与输入

`RuntimeToolName` 首版只允许：

- `resolve_symbol`
- `find_call_chain`
- `query_code_graph`

每个 `RuntimeAction` 包含跨轮唯一 ID、Tool、意图、预期信息和类型化输入。Action ID 由
当前计划轮次约束为 `r{round}a{index}`，不能复用单轮模式的裸 `q1`。

输入类型分别为：

- `ResolveSymbolActionInput`：方法 anchor，以及可选 graph version、service、HTTP method、
  API path 限定。
- `FindCallChainActionInput`：anchor、可选限定和 local/REST/MQ hop 上限。
- `QueryCodeGraphActionInput`：自然语言 question template、intent、required information、
  可选 anchor/query shape，以及模板变量到 Evidence Binding 的映射。

Tool 参数中需要消费历史结果的字符串字段使用 `ActionValue`：它只能是字面量或
`EvidenceBinding(entity_id, field)`。`query_code_graph` 的 question template 使用具名
占位符；每个占位符必须恰好有一个 Binding，运行时解析后再构造 `GraphQueryRequest`。

Binding 只能引用此前轮次成功 Observation 中 Planner 可见的 Key Entity 字段：

```text
r1a1 -> observation o1 -> entity o1:e1
r2a1.anchor = {"entity_id": "o1:e1", "field": "qualified_name"}
```

不得引用当前轮 Action、原始 row 的隐藏字段、失败 Observation 或不存在的字段。无法解析
的 Binding 生成失败 Observation，不调用 Tool。

### 2.3 Observation 与 Evidence

`ObservationStatus` 包含 `success`、`empty`、`failed` 和 `duplicate`。

每个 `Observation` 保存：

- observation/action ID 和 Tool 来源；
- 状态、紧凑摘要、返回行数、截断标记；
- Planner 可消费的 `KeyEntity`；
- 固定错误分类和经过长度限制的安全错误消息；
- 原始 Tool 结果，供审计和最终结构化返回使用。

原始结果可以是完整 `ExecutedCypher` 或完整 `ResolvedMethod` tuple。完整 Cypher、全部 rows、
traceback 和底层数据库诊断不得进入 Planner 或 Answerer prompt。

Observation compactor 使用确定性投影：识别方法全限定名、类、微服务、API、HTTP method、
消息队列、交换机、路由键和调用路径等字段；为投影结果分配稳定的
`{observation_id}:e{index}`。未知表格仍可投影为数量受限的结构化记录。Planner view 包含
状态、摘要、行数、列名、截断、关键实体和错误元数据，并受实体数量与文本长度上限约束。

“新证据”使用规范化 Key Entity/结果记录的稳定 JSON 指纹判断，忽略 Action ID、Tool 名、
Cypher 文本、执行耗时和字段顺序。相同事实由不同查询再次返回不算新增证据；首次明确的
空结果可作为一个 Observation 事实，但重复空查询会先被重复 Action Guard 阻断。

### 2.4 Run 与终止原因

`IterativeRun` 返回规范化问题、所有计划版本、所有 Observation、最终 obtained/missing、
已执行轮次、已消费 Action 数、停止原因和最终 `ResultSummary`。

`IterativeStopReason` 至少包含：

- `complete`
- `max_rounds`
- `action_budget_exhausted`
- `repeated_actions`
- `no_new_evidence`
- `planner_failed`
- `capability_exhausted`

无论正常完成还是被 Guard 截停，Answerer 都基于 Planner 可见的紧凑证据生成一次答案。
非正常结束必须明确说明缺失信息和停止原因，不能把部分证据包装成完整结论。Answerer 失败
时返回模板化 `ResultSummary`，不得丢失结构化运行结果。

## 3. Planner 和 Tool 路由政策

Planner prompt 输入：原始问题、下一执行轮次、剩余轮次和 Action 预算、此前全部紧凑
Observation、累计 obtained/missing，以及现有 Primary Agent 语义能力视图。数据库返回
文本放在明确的数据边界内，并声明其不能修改系统规则或 Tool 权限。

Tool 选择优先级：

1. 需要把名称或 API 锚点解析为稳定方法实体时，使用 `resolve_symbol`。
2. 需要完整方法/入口调用链时，使用 `find_call_chain`。
3. 其他已有确定性形状仍通过 `query_code_graph` 进入 Graph Query Engine；Engine 会先尝试
   现有 deterministic compilers。
4. 只有无法用已知确定性能力表示的通用图查询，才进入 Text2Cypher 翻译。

`query_code_graph` Action 显式声明 `full_method_call_chain` 时视为非法计划，防止绕过已有
确定性 Tool。Tool 执行始终顺序进行；同一批中一个 Tool 失败不会阻止其余不依赖 Action，
但失败会成为下一次 Planner 可见的结构化 Observation。

## 4. Runtime 状态机与 Loop Guard

默认限制：

- `max_rounds = 3`
- `max_actions = 9`
- `max_actions_per_plan = 3`
- `max_consecutive_no_evidence_rounds = 2`

执行顺序：

1. 规范化并校验问题，获取一次 Schema。
2. 调用 Planner 生成下一执行轮次的计划。
3. 若 complete，结束检索并调用 Answerer。
4. 若 continue，按顺序处理 Action；每个 Action 在执行前解析 Binding、校验参数并计算
   canonical fingerprint。
5. 已出现过的 fingerprint 不执行，记录 duplicate Observation，并消耗一个 Action 预算。
6. Tool 成功、空结果、参数/Binding 失败或 Tool 异常均生成 Observation。只捕获普通
   `Exception`；进程级中断不吞掉。错误不触发整题重跑。
7. 一轮结束后合并新证据：有新证据则无进展计数归零，否则加一。
8. 若本轮所有 Action 都重复，立即以 `repeated_actions` 停止；否则连续两轮无新证据时以
   `no_new_evidence` 停止。
9. 若计划 Action 超过剩余预算，只执行按计划顺序可容纳的前缀，然后以
   `action_budget_exhausted` 停止；超出部分不调用 Tool。
10. 每轮执行后再次调用 Planner；完成则停止，否则进入下一轮。第三轮执行完仍不 complete
    时记录其最后 obtained/missing，并以 `max_rounds` 停止，不执行新的 Action。
11. 终止后调用一次 Answerer，返回 `IterativeRun`。

Action 预算统计所有已进入处理流程的 Action，包括成功、空、失败、Binding 失败和重复；
没有进入预算前缀的 Action 不计数。Tool 自己的 Transport Retry 和 Query Recovery 不增加
Action 计数。

## 5. 代码组织与装配

- 在领域模型和 ports 中加入上述稳定契约，不修改现有单轮模型。
- 新增纯 Prompt builder/response parser，以及基于共享 `LLMClient` 的
  `LLMIterativePlanner` 和 `LLMIterativeAnswerer`。
- 新增 Observation compactor、Binding resolver/Tool dispatcher 和 `IterativeRuntime`。
- factory 抽取现有 Gateway/Engine/Tool 栈的私有共享装配，避免 SingleRound 与 Iterative
  复制安全链路；新增 `build_iterative_runtime(...)`。
- `build_pipeline_from_components()` 继续只构建 `SingleRoundRuntime`。现有 bootstrap、CLI、
  evaluation、`Text2CypherResponse` 和资源关闭行为不变。
- 从 `text2cypher.runtime` 导出新 Runtime 和必要公共类型，使调用方可以显式构造和运行。

## 6. 测试与验收

必须覆盖以下核心场景：

1. **单轮即可完成**：初始计划产生 Action，执行一次后 Planner 判断 complete；Tool 和
   Answerer 调用次数正确。
2. **第二轮补充信息**：第一轮解析实体，第二轮通过 Key Entity Binding 把全限定名传给
   下一个 Tool，证明存在真实数据依赖。
3. **重复查询阻断**：不同 Action ID、相同解析后 Tool 参数只执行一次，第二次记录
   duplicate 并以明确原因停止。
4. **预算耗尽**：注入较小 `max_actions`，只执行允许的 Action 前缀，未超额调用 Tool，
   返回带缺失信息的部分答案。
5. **Tool 失败后重新规划**：失败 Observation 进入下一次 Planner；已成功 Action 不重跑，
   替代 Action 可以完成任务。

补充测试覆盖：

- complete/continue JSON 不变量、Action ID、Tool 输入和非法 Tool。
- 跨轮 Binding 成功及未知实体、未知字段、当前轮引用失败。
- Observation 保留原始结果，但 Planner/Answerer prompt 不出现完整 Cypher、全部 rows 或
  traceback。
- 空结果、截断结果、连续无新证据、部分重复与全部重复。
- Query Engine 的 Corrector/Retry 不增加 Runtime 轮次。
- 显式 iterative factory 使用同一个只读 Gateway/Engine/Tools；SingleRound factory、Pipeline、
  CLI 和 close callback 回归不变。

验证顺序：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests evaluation
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m mypy
```

真实 Neo4j/LLM acceptance 继续遵循现有显式环境开关，不以离线 mock 测试替代真实语义
验收。实施前相关 baseline 测试为 72 passed。
