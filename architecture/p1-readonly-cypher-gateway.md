# P1：抽取只读 Cypher 安全执行入口

## 目标

将当前散落在 `Text2CypherPipeline` 初始执行和修正执行路径中的 Parser → Validator → Executor 顺序抽取为可复用的 Deterministic Graph Core 入口。该阶段不改变查询生成、恢复条件、异常类型、Neo4j 配置、重试、超时、结果截断或公开 CLI 行为。

## 已确认事实

- Pipeline 在首次候选和 Corrector 候选上重复执行同一条 Parser → Validator → Executor 链。
- Parser、Validator 和 Executor 已经通过领域端口隔离；Validator 和 Executor 分别承担 EXPLAIN 只读检查和读取事务限制。
- Parser、Validator、Executor 抛出的 `CypherParseError`、`CypherValidationError`、`CypherExecutionError` 被 Pipeline 用于决定是否调用 Corrector；错误类型和附带的 Neo4j failure context 必须原样保留。
- Schema 查询属于受信任的基础设施操作，不能通过新入口绕过或放宽模型生成 Cypher 的准入规则。

## 实施

1. 新增领域 `ExecutedCypher` 值对象与 `ReadOnlyCypherGateway` 端口，表达“候选已被解析、准入并执行”的最小事实。
2. 新增 `graph_core` 包与默认 gateway。它只依赖现有 Parser、Validator、Executor 端口，按固定顺序调用，不新增重试或恢复逻辑。对于三种可恢复错误，它内部保留“原始或已解析候选 + 原异常”的失败载体；Pipeline 在对外传播或进入 Corrector 前取回原异常，因此原有异常类型和 failure context 保持不变。
3. `Text2CypherPipeline` 继续接受已有三个端口参数；新增可选 gateway 注入。没有注入时由这三个端口构造默认 gateway，因此旧调用方和测试不需要变更。
4. Pipeline 的首次候选与 Corrector 候选都改经 gateway 执行。保留当前 parse、validation、execution 三类错误的 Corrector 分支，保留空结果复核及其“只在修正结果非空时替换”的行为。
5. 生产 bootstrap 显式构造并注入默认 gateway，证明生产路径已通过 Core；evaluation 现有包装器仍包裹 Parser、Validator、Executor，因此其观测顺序和指标不变。

## 验收与回滚

- Gateway 单元测试覆盖成功顺序、解析失败停止、校验失败不执行、执行异常原样传播。
- Pipeline 回归测试覆盖初始和修正候选均经过 gateway，并保持 Corrector 错误类型与空结果策略。
- 全量 pytest、Ruff、Mypy 通过。
- 该阶段仅增加 Core 入口并改接线；回退该提交即可恢复 Pipeline 直接调用三个端口的路径。
