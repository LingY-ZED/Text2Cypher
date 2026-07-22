# Text2Cypher

面向代码知识图谱的从零实现 Text2Cypher 工程基础。项目不会调用 Neo4j GraphRAG、
Text2CypherRetriever 或其他现成 Text2Cypher 服务；仅使用官方 Neo4j Python Driver
和可配置的 OpenAI 兼容 LLM API。

当前版本是可测试的项目骨架，尚未接入真实 Neo4j 或 LLM。完整实施顺序见根目录
todo.md。

## 目标流程

用户问题
→ SchemaFetcher
→ PromptBuilder
→ LLMClient
→ CypherParser
→ CypherValidator
→ CypherExecutor
→ ResultFormatter

Text2CypherPipeline 通过依赖注入编排这七个阶段。真实 I/O 适配器完成前，测试中的
fake 组件用于验证编排和错误边界。

## 安装与检查

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
ruff check .
mypy
text2cypher --help
~~~

复制 .env.example 为 .env 后再填写本地凭据。真实密码和 API Key 不应提交，也不应
写入日志。

## 当前 CLI

~~~powershell
text2cypher ask "ts-food-service 有哪些 API 端点？"
~~~

当前命令会在配置通过后提示生产适配器尚未完成；这是预期行为。完成 todo.md 的 P0
任务后，该命令将执行完整只读查询路径。

## 架构约束

- GraphSchema 是结构化模型；PromptBuilder 只消费其稳定渲染结果。
- SchemaFetcher 后续使用 Neo4j 内置 Schema 过程，不依赖目标库中不存在的 APOC
  meta 过程。
- 任何模型生成的 Cypher 必须先通过本地安全检查和 EXPLAIN 的只读类型检查，才能
  执行。
- 只读路由是附加保护；生产环境仍应使用最小权限数据库账号。

