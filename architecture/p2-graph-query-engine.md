# P2：抽取单次 Graph Query Engine

## 目标

把“一条已规划自然语言查询”的 Few-shot 路由、Prompt 构造、Cypher 生成、只读执行和兼容恢复从 `Text2CypherPipeline` 抽取为无状态的 Graph Query Engine。Pipeline 保留问题规划、子查询顺序、结果总结和格式化。

## 已确认事实

- 现有 `_run_sub_query` 同时完成 Router、Prompt、LLM、Core 调用和恢复，阻碍后续 Tool 直接复用单次查询能力。
- Router 和 PromptBuilder 对 `PrimaryAgentQuery` 使用 `route_planned` 与 `build_planned`，普通兼容实现则使用问题文本；P2 必须保留这个运行时分派。
- Corrector 需要得到解析失败时的原模型输出，以及验证或执行失败时的规范化 Cypher。P1 gateway 已保留这一区别。
- `log_correction_event` 的结构化 payload 是 evaluation 的观测输入。事件字段和调用次数不能因移动 logger 所在模块而改变。

## 实施

1. 新增 `GraphQueryRequest` 和 `QueryContext` 领域值对象，以及 `GraphQueryEngine` 端口。请求字段一一对应现有 `PrimaryAgentQuery`，提供无损双向适配；上下文在本阶段只包含一次运行中已获取的 `GraphSchema`。
2. 新增无状态默认 Engine，依次执行：planned/legacy Router 分派、planned/legacy Prompt 分派、LLM 生成、P1 gateway、一次错误修正、可选空结果复核。返回 `ExecutedCypher`。
3. 将恢复逻辑与 failure-kind/context 映射迁到 Engine。P1 的失败载体只在 Engine 内部使用；对外仍抛出原有 parse、validation、execution、连接或访问异常。
4. Pipeline 新增可选 Engine 注入。未注入时从原有构造参数创建默认 Engine，保证旧 Python 调用方继续工作；Pipeline 把 `PrimaryAgentQuery` 适配为请求并将 Engine 结果投影为 `SubQueryResponse`。
5. production bootstrap 显式构造同一组 PromptBuilder、LLM、gateway、Router、Corrector 和 Engine；evaluation 继续装饰原有端口，以保持事件顺序、逻辑调用次数和指标语义。

## 验收与回滚

- Engine 测试覆盖 planned 和 legacy 分派、正常顺序、三类可修复错误、Corrector 候选重新准入、空结果保留/替换、连接错误不纠正。
- Pipeline 回归继续覆盖多子查询 fail-fast、总结只在全部成功后执行和旧构造方式。
- 固定响应替身下，旧 Pipeline 与 Engine 接线后的 Prompt、Cypher、结果、事件和调用次数相同。
- 全量 pytest、Ruff、Mypy 通过；真实评测留待完整外部基线采集。
- 回退本阶段提交即可恢复 Pipeline 直接编排单次查询。
