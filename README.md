# Text2Cypher

这是一个从零实现的 Neo4j Text2Cypher 最小原型。项目不会调用 Neo4j GraphRAG、
Text2CypherRetriever 或其他现成的 Text2Cypher 服务；图数据库访问仅使用官方
Neo4j Python Driver，模型调用仅使用通用 OpenAI 兼容聊天补全 API。

当前已接通安全的最小闭环：动态获取 Schema、生成 Cypher、解析、只读校验、执行和
结构化结果输出，并支持基于实时 Schema 过滤和本地混合排序的动态 Few-shot。

## 流程

```text
用户自然语言问题
  → SchemaFetcher
  → SchemaGraphBuilder
  → FewShotSelector
  → PromptBuilder
  → LLMClient
  → CypherParser
  → CypherValidator
  → CypherExecutor
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

Few-shot 默认启用，最多选择 3 条与实时 Schema 兼容且与问题相关的示例：

```dotenv
TEXT2CYPHER_FEW_SHOT_ENABLED=true
TEXT2CYPHER_FEW_SHOT_TOP_K=3
TEXT2CYPHER_FEW_SHOT_MIN_SCORE=0.18
TEXT2CYPHER_FEW_SHOT_MAX_CHARS=3500
TEXT2CYPHER_FEW_SHOT_LIBRARY_PATH=
```

空路径使用包内 18 条黄金示例；设置外部 JSON 路径可替换示例库。设置
`TEXT2CYPHER_FEW_SHOT_ENABLED=false` 会跳过示例文件加载并恢复 Zero-shot。
数据格式、兼容规则和扩展方法见
[Few-shot 设计](docs/few-shot-design.md)。

## 运行

```powershell
text2cypher ask "ts-food-service 有哪些 API 端点？" --json
```

也可以使用模块入口：

```powershell
python -m text2cypher ask "图谱中有哪些微服务？"
```

输出包含生成的 Cypher、列名、JSON 友好记录、截断标记和执行耗时。

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
