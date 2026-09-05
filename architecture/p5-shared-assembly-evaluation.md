# P5：共享装配与评测接入计划

## 目标与范围

生产 `application.bootstrap` 和 `evaluation.run` 目前各自拼装 Schema、Prompt、
LLM、Parser、Validator、Executor、Router、Corrector、Summarizer 和 Pipeline。即使
端口相同，两套调用路径仍可能逐步偏离。P5 将它们收敛到一个共享的应用装配 factory：

```text
生产适配器 ─┐
评测装饰适配器 ─┼─> PipelineComponents
               └─> build_pipeline_from_components
                    Gateway → Engine → Tools → SingleRoundRuntime → Pipeline
```

factory 只负责组合已有端口，不创建连接、不读取环境变量、不加载未注入的资源，也不
增加新的外部调用。生产 bootstrap 仍负责配置、Driver/LLM 生命周期和真实适配器创建；
评测仍负责创建同一类外部适配器的记录装饰器及 Oracle 预检。

## 共享契约

`PipelineComponents` 以显式字段携带每个端口和行为开关：SchemaFetcher、PromptBuilder、
LLM、Parser、Validator、Executor、Formatter、可选 Planner/Decomposer、Few-shot Router、
Corrector、Summarizer、空结果恢复开关和关闭 callback。factory 是唯一的
Gateway → Engine → Tool → Runtime → Pipeline 装配位置。

旧 `Text2CypherPipeline` 构造参数仍保留。其兼容分支将旧依赖投影为
`PipelineComponents` 后调用同一 Runtime factory，不再私自复制 Engine 与 Gateway 的
装配逻辑。生产和 evaluation 则直接调用完整 factory，避免各自再走兼容分支。

## 评测观测

评测通过显式注入 `PipelineComponents` 的装饰端口来记录 LLM、Primary Agent、Router、
Parser、Validator 和 Executor；生产使用未装饰的同类端口。评测日志事件改由
`EvaluationLogObserver` 的生命周期范围安装到明确的业务 logger，而不是向 root logger
临时添加全局 Handler。该 observer 只投影已经脱敏的结构化事件，退出后恢复原 logger
级别和 handler 状态。

这不会改变生成、查询、纠错、总结或数据库调用次数。P0 已加入的 evaluator SHA、数据
集哈希和资源哈希继续写入 metadata；报告对缺少这些字段的历史记录仍以 `unknown` 安全
显示，因此旧评测文件仍可读取。

## 验证与回滚

新增 factory 测试验证显式注入的 Engine/Tool 链被使用，新增 observer 测试验证事件捕获
及 logger 状态恢复。现有 Bootstrap、Pipeline、evaluation instrumentation/provenance/report
测试继续执行，并运行 Ruff、pytest 与 mypy。

本阶段不触发真实 Neo4j/LLM 评测；这些显式开关的集成与 acceptance 项保持跳过，不能把
离线结构验证表述为真实语义验收。回滚时可独立撤销 factory/评测接线提交，生产 bootstrap
和 compatibility facade 可恢复到 P4 的显式装配，且不影响已保存评测报告格式。
