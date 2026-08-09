# Text2Cypher

这是一个从零实现的 Neo4j Text2Cypher 最小原型。项目不会调用 Neo4j GraphRAG、
Text2CypherRetriever 或其他现成的 Text2Cypher 服务；图数据库访问仅使用官方
Neo4j Python Driver，模型调用仅使用通用 OpenAI 兼容聊天补全 API。

当前已接通安全的最小闭环：动态获取 Schema、生成 Cypher、解析、只读校验、执行和
结构化结果输出，并支持 Schema 感知的问题拆分、LLM Router 动态 Few-shot 和受控错误恢复。

## 流程

```text
用户自然语言问题
  → SchemaFetcher
  → SchemaGraphBuilder
  → QuestionDecomposer
  → 按依赖 DAG 的拓扑层执行：
      同层独立子问题并行；后继子问题以受控 Neo4j 参数消费父结果
      FewShotRouter
      → PromptBuilder
      → LLMClient
      → CypherParser
      → CypherValidator
      → CypherExecutor
      → 一次性 Cypher Corrector（仅在失败或空结果复核时）
  → ResultFormatter
```

## 目录职责

```text
src/text2cypher/
├── domain/                 # 不可变模型、端口协议、阶段化异常
├── application/            # 流程编排与生产装配
├── components/             # Prompt、Parser、Formatter 等纯处理组件
├── infrastructure/
│   ├── neo4j/              # Driver、Schema、校验、执行适配器
│   ├── llm/                # OpenAI 兼容模型客户端
│   └── few_shot/           # 版本化 JSON 示例库加载
├── resources/              # 包内默认 Few-shot 黄金示例
├── interfaces/             # 命令行入口
├── config.py               # 环境变量配置
└── __main__.py             # python -m 入口
```

领域层不依赖 Neo4j、HTTP 或命令行；应用层只依赖领域端口；外部 I/O 全部位于基础设施层。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

将 `.env.example` 复制为 `.env`，在本地填写 Neo4j 连接信息和模型 API Key。`.env`
已被忽略，禁止提交真实密码或 API Key。

DeepSeek V4 Flash 可通过 `TEXT2CYPHER_LLM_MAX_TOKENS` 限制单次输出；
`TEXT2CYPHER_LLM_DISABLE_THINKING` 是仅在服务商支持时才发送的可选扩展字段。默认
保留模型自身的思考策略；遇到外部服务响应较慢时，可在本地按需调整超时和该开关。

问题拆分默认启用。Decomposer 使用完整动态 Schema，将问题规划成一到三个可带依赖关系的
子问题。依赖节点只消费系统提供的 `LIST<MAP>` Neo4j 参数，真实数据库值不会进入模型
Prompt；同一拓扑层的独立节点最多并行执行三个完整分支。拆分失败时回退为原问题，不影响
原有单查询能力：

```dotenv
TEXT2CYPHER_QUESTION_DECOMPOSITION_ENABLED=true
TEXT2CYPHER_QUESTION_DECOMPOSITION_MAX_SUBQUESTIONS=3
TEXT2CYPHER_SUBQUERY_MAX_WORKERS=3
```

Decomposer、Few-shot Router 和最终 Cypher 生成复用同一个模型客户端。简单问题在
Few-shot 开启时最多调用模型 3 次；三个子问题最多调用 7 次。关闭拆分后仍返回统一的
单元素 `sub_queries` 结构，但不会产生 Decomposer 模型调用。

Few-shot 默认启用。系统先按实时 Schema 过滤候选，再使用与 Cypher 生成共享的模型
选择最多 3 条相关示例；Router 不可用或返回无效内容时自动回退 Zero-shot：

```dotenv
TEXT2CYPHER_FEW_SHOT_ENABLED=true
TEXT2CYPHER_FEW_SHOT_TOP_K=3
TEXT2CYPHER_FEW_SHOT_MAX_CHARS=3500
TEXT2CYPHER_FEW_SHOT_LIBRARY_PATH=
```

空路径使用包内 18 条黄金示例；设置外部 JSON 路径可替换示例库。Router 只接收通过
实时 Schema 兼容过滤的候选元数据。设置 `TEXT2CYPHER_FEW_SHOT_ENABLED=false` 会跳过
示例文件加载并恢复 Zero-shot。

## 错误恢复

LLM 和 Neo4j 的超时、连接故障、服务限流及 5xx 等瞬态错误默认最多尝试三次，退避为
`0.5s → 1.0s`；所有其他错误不会被盲目重试。LLM 返回无效 JSON、没有候选或空文本时
也会被视为可重试响应，因此“模型响应不包含文本内容”会在最终报错前自动重试。

```dotenv
TEXT2CYPHER_RETRY_ENABLED=true
TEXT2CYPHER_RETRY_MAX_ATTEMPTS=3
TEXT2CYPHER_RETRY_BASE_DELAY_SECONDS=0.5
TEXT2CYPHER_RETRY_MAX_DELAY_SECONDS=4.0

TEXT2CYPHER_CYPHER_CORRECTION_ENABLED=true
TEXT2CYPHER_EMPTY_RESULT_CORRECTION_ENABLED=true
```

对于最终 Cypher 的解析、只读校验或执行错误，系统最多调用一次共享模型进行修正，
修正版仍必须重新通过 Parser、EXPLAIN 和只读执行。安全执行但零行的查询也可复核一次；
只有修正版返回非空结果才会替换原结果。关闭纠错开关可恢复原有直接失败行为。

恢复事件只在 stderr 输出结构化 JSON 日志，stdout 的 `--json` 查询结果不变。日志不会
包含问题、Prompt、Cypher、数据库结果、服务响应正文或凭据。

## 运行

```powershell
text2cypher ask "ts-food-service 有哪些 API 端点？" --json
```

也可以使用模块入口：

```powershell
python -m text2cypher ask "图谱中有哪些微服务？"
```

简单和复杂问题统一返回分组结果：

```json
{
  "question": "查询某方法的上游和下游",
  "status": "partial",
  "decomposed": true,
  "sub_query_count": 2,
  "successful_count": 1,
  "failed_count": 0,
  "blocked_count": 1,
  "sub_queries": [
    {
      "id": "q1",
      "depends_on": [],
      "parameter_sources": {},
      "status": "success",
      "question": "查询该方法的上游",
      "cypher": "MATCH ...",
      "columns": ["上游"],
      "rows": [],
      "row_count": 0,
      "truncated": false,
      "duration_ms": 120
    },
    {
      "id": "q2",
      "depends_on": ["q1"],
      "parameter_sources": {
        "dep_q1_rows": {"source_id": "q1", "columns": ["上游"]}
      },
      "status": "blocked",
      "question": "查询该方法的下游",
      "cypher": null,
      "columns": null,
      "rows": null,
      "row_count": null,
      "truncated": null,
      "duration_ms": null,
      "error": {"kind": "dependency_failed", "message": "依赖子查询未成功完成"}
    }
  ]
}
```

每个分组包含节点 ID、依赖来源、状态、对应问题、Cypher、列名、JSON 友好记录、截断标记
和执行耗时。一个独立分支失败不会影响其他独立分支；其后继会标记为 `blocked`。至少一个
分支成功时仍输出 `partial` 响应，CLI 返回退出码 5；全部失败仍使用运行错误退出码 4。系统
不做跨子查询结果联结、去重或自然语言总结。

## 安全边界

- Schema 通过 Neo4j 内置过程动态读取；不依赖 APOC。
- 校验器拒绝空语句、注释、多语句、写入、管理、过程调用和外部数据加载语句。
- Cypher 必须以受限只读子句开始，并通过 `EXPLAIN` 返回的只读查询类型检查。
- 执行器使用指定 database、读取路由、事务超时与结果行数上限。
- 建议为运行账户配置最小只读权限；应用层校验是额外保护，不替代数据库权限。

## 检查

```powershell
pytest
ruff check .
mypy
```

默认测试不会访问真实数据库。需要执行只读集成测试时：

```powershell
$env:TEXT2CYPHER_RUN_INTEGRATION="1"
pytest tests\integration\test_neo4j_readonly.py
```

该集成测试只读取动态 Schema、执行 `RETURN 1`，并对 18 条黄金示例逐条执行
`EXPLAIN` 和真实查询，检查精确列名、非空结果、黄金行数及关键实体；不会写入或修改
图数据。数据库快照变化后应显式复核并更新黄金结果。

真实自然语言验收还可显式执行：

```powershell
$env:TEXT2CYPHER_RUN_ACCEPTANCE="1"
$env:TEXT2CYPHER_LLM_DISABLE_THINKING="true"
pytest tests\integration\test_text2cypher_acceptance.py
```

该测试会调用真实模型与数据库，覆盖原有五类问题、直接方法调用问题和复合上下游影响
问题；若模型服务网络暂不可用，对应用例会标记为跳过。阶段设计见
[Phase 03 计划](plans/phase-03-p2-few-shot.md)。
