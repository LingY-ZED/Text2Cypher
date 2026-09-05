# P4：SingleRound Runtime 与内部 Tools 迁移计划

## 目标与范围

P3 后，领域知识已由 `skills` 和当前 Graph Profile 提供，单次自然语言图查询
已由 `GraphQueryEngine` 负责。但 `Text2CypherPipeline` 仍同时管理 Schema 获取、
计划、子查询执行、总结、格式化和资源关闭。本阶段将单轮编排抽取为
`SingleRoundRuntime`，并以两个明确的内部 Tool 包装已有确定性边界：

```text
Text2CypherPipeline（兼容门面）
  → SingleRoundRuntime（计划、顺序执行、收集、总结）
      → GetSchemaTool（SchemaFetcher）
      → QueryCodeGraphTool（GraphQueryEngine）
          → ReadOnlyCypherGateway → Neo4j
  → ResultFormatter（仅展示）
```

本阶段不实现迭代 Runtime、跨步骤结果绑定、工具恢复、MCP/API 或专项确定性查询。
`QueryCodeGraphTool` 只是当前单次自然语言图查询的明确入口；它每次只接收一条
`GraphQueryRequest` 与固定 `QueryContext`，不会重新调用 Primary Agent 或总结整题。

## 当前行为契约

从当前 `Text2CypherPipeline` 与测试可确认的契约：

1. 先规范化问题，然后只获取一次 Schema；Primary Agent 不接收 Schema，旧
   Decomposer 保持接收 Schema。
2. 计划为一至三条自包含 `PrimaryAgentQuery`，按 `q1…qn` 顺序执行；每条通过
   `GraphQueryRequest.from_primary_agent_query()` 保留 planned Prompt/Router 的对象语义。
3. 任一子查询失败立刻停止，后续查询、总结和格式化均不运行。
4. 全部查询成功后最多调用一次 Summarizer；随后 Formatter 使用相同问题、顺序和
   Summary 构造现有 `Text2CypherResponse`。
5. 关闭由 Pipeline 的现有 callback 持有，重复 `close()` 与上下文管理器安全。

## 设计

### Runtime result

新增 `SingleRoundRun`，包含规范化问题、可审计 `PrimaryAgentPlan`、顺序保持的
`SubQueryResponse` 和可选 `ResultSummary`。它明确不含 `formatted` 字符串；展示是
接口门面的责任。结果模型仍限制一至三条查询，且计划原问题必须等于运行问题。

### Tools

`GetSchemaTool.get_schema()` 仅委托受信任的 `SchemaFetcher.fetch()`，不会把 Schema
过程开放为模型生成 Cypher 的权限例外。

`QueryCodeGraphTool.query(request, context)` 仅委托 `GraphQueryEngine.query()`。因此
当前 Prompt、Few-shot、Cypher Correction 与 ReadOnly Gateway 的调用顺序不变；Tool
自身不增加 LLM、数据库重试或恢复循环。

为 Runtime 添加面向这些 Tool 的 Protocol，生产和测试均可显式注入；端口只描述
输入输出，不能被当成新的通用 RPC 机制。

### Pipeline 和装配

`Text2CypherPipeline` 保留类名、`run()`、`close()`、上下文管理器和旧构造参数。
它默认将旧注入组装为 Gateway → Engine → Tools → Runtime；也接受显式注入的
`SingleRoundRuntime`，以便新装配不再把整套旧依赖传进门面。

生产 bootstrap 显式装配 Engine、Tools 与 Runtime，再把 Runtime 和 Formatter 交给
Pipeline。evaluation 暂时仍通过旧构造路径注入记录装饰器，避免在 P4 中同时改变评测
装配；P5 再收敛为共享 factory 与显式观测注入。

## 验证与回滚

现有 Pipeline、CLI、Bootstrap、Summarizer、Formatter、Recovery 测试继续作为兼容
基线。新增 Runtime/Tool 测试验证 Schema 只取一次、请求与上下文完整转交、计划顺序、
第二条失败时不总结，以及 Pipeline 从运行结果格式化。运行 Ruff、pytest 和 mypy；不
运行需要显式启用的真实 Neo4j/LLM 集成验收。

回滚边界是本阶段独立提交：删去 Runtime/Tools 接线即可恢复 Pipeline 内部编排；
Graph Query Engine、Graph Core、资源和对外 CLI 契约均不改变。
