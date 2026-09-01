# Text2Cypher 业务规则提示词模块化重构计划

## 1. 背景与目标

当前代码知识图谱的物理业务规则集中在 code_graph_business_rules.md 中，并被 Cypher Translator、Few-shot Router 和旧 Question Decomposition Prompt 整体注入。该文件同时包含锚点、归属、方法调用、API、REST、MQ、有序路径、聚合和拆分规则，导致简单查询也携带全部业务上下文。

本次重构的目标是：

- 将物理业务规则按查询场景拆成可独立加载的模块；
- Cypher Translator 和 Few-shot Router 始终加载 core，其余模块按子问题场景选择；
- Primary Agent 继续使用完整但精简的抽象规划能力，不接收物理图 Schema 或 Cypher 模式；
- 保持现有业务语义、动态 Schema 优先级、只读安全边界和查询返回行为不变；
- 保持 pipeline、领域端口和 Primary Agent JSON 契约稳定；
- 通过单元测试证明无关规则不会进入 Prompt，同时不丢失任何现有有效规则。

## 2. 关键架构决策

### 2.1 不在 Primary Agent 之前进行场景分类

Primary Agent 本身负责识别用户问题的意图、方向、范围、返回形状以及是否需要拆分。在 Primary Agent 之前根据关键词或 QueryShape 选择规划规则，会产生以下问题：

- 场景分类依赖尚未完成的意图理解，形成循环依赖；
- 原始复合问题可能同时包含 REST、入口 API、方法调用和聚合等多个场景；
- 用户改写或隐含表达容易被词法分类漏判；
- 若增加一次 LLM 分类调用，会提高延迟、成本和 pipeline 复杂度。

因此 Primary Agent 始终加载现有抽象能力说明和规划核心约束。该能力说明覆盖全部可规划场景，但不包含精确标签、关系、属性、MATCH 或其他 Cypher 语法。Primary Agent 先产生一到多个自包含子问题，后续阶段再针对每个子问题选择物理规则。

### 2.2 两层规则模型

规则分为两层：

1. 规划层：由 Primary Agent 使用，描述可检索能力、拆分边界、方向和返回形状，不描述物理图实现。
2. 翻译层：由 Cypher Translator 使用，描述精确标签、关系方向、属性、路径约束和投影规则。

Few-shot Router 位于两层之间。它接收 Primary Agent 输出的自包含子问题，只使用精简的路由规则来区分锚点、关系方向、查询形状和返回形状，不需要物理 Cypher 细节。

### 2.3 不新增 LLM 调用和公开契约字段

本次不增加前置分类器，也不在 PrimaryAgentQuery 中增加 query_shape、rule_modules 或 requested_fields。Primary Agent 的响应仍只包含：

- question；
- intent；
- required_information。

现有 PrimaryAgentPlan、PromptBuilder、FewShotRouter 和 Text2CypherPipeline 的公开端口保持不变。

## 3. 规则模块划分

物理规则拆为以下九个模块。core 固定排在第一位，其余模块按稳定顺序拼接。

| 模块 | 规则内容 |
| --- | --- |
| core | 动态 Schema 最高优先级、结果事实对应性、非聚合去重通则、废弃标签/属性/关系禁用 |
| anchor_ownership | 方法、类、微服务锚点；短类名与完整方法名；服务名；归属链；接口实现方向；方法身份投影 |
| method_call | 接口分派、相邻方法调用、直接上下游、上游可达集合、最短调用距离及调用方向 |
| entry_api | 服务公开 API、服务于关系、可达入口 API、API 路径和 HTTP 请求语义 |
| rest | 方法 REST 出口、目标服务、外部入口映射、可选映射、调用服务和目标方法语义 |
| ordered_path | 链中下一节点、路径签名、位置索引、完整入口链和完整下游链的 UNION 规则 |
| mq | 消息发布、交换机路由、队列消费、路由键、发送/接收服务和发布调用点 |
| aggregation | 分组列、计数对象、REST 调用关系数及其他聚合返回约束 |
| decomposition | 方法变更三集合拆分、完整链保持单查询、外部服务的固定解释 |

迁移时只调整文件和标题组织，不改写已有物理规则的业务含义。每条现有有效规则必须归入且只归入一个主模块；跨模块场景通过 selector 的依赖闭包组合，不通过复制规则解决。

### 3.1 core 边界

core 仅保留所有翻译场景都适用的约束，避免把 REST、MQ 或路径细节重新放回常驻模块。动态 Schema 和只读约束仍以 DefaultPromptBuilder 的 system instruction 为最高优先级；core 中保留原业务规则对动态 Schema 的优先级说明，但不得削弱或覆盖已有安全指令。

### 3.2 跨模块规则归属

- 可达入口 API 的入口绑定和投影规则归 entry_api；其调用可达性依赖 method_call。
- 完整入口链和完整下游链的路径构造规则归 ordered_path；selector 为其补齐 method_call、entry_api 和 rest。
- REST 调用数量的具体计数对象归 aggregation；REST 的关系方向和服务角色仍归 rest。
- MQ 发送和接收服务的角色列归 mq；通用逐行对应和去重要求归 core。
- 方法变更题的拆分规则归 decomposition；其物理子查询仍分别组合 method_call、entry_api 和 rest。

## 4. 规则加载与选择

### 4.1 Loader

新增缓存式 module loader，职责如下：

- 按模块名从包资源读取 UTF-8 Markdown；
- 对内容 strip 后拒绝空模块；
- 读取失败继续抛出 CodeGraphBusinessRulesError；
- 按固定模块顺序渲染 Prompt，保证测试和缓存结果确定；
- 同一 Prompt 中每个模块最多出现一次。

保留无参 load_code_graph_business_rules()。该兼容 API 返回全部 Translator 模块的固定顺序拼接，用于兼容现有内部调用、完整性测试和诊断工具；生产 Prompt 不再调用该 API 注入全量规则。

### 4.2 Selector 输入

Selector 只在 Primary Agent 完成后工作，输入包括：

- 当前自包含子问题；
- resolve_query_shape 得到的现有 QueryShape；
- 对 Translator 而言，可额外使用已选 Few-shot 的 category、tags 和 query_shape；
- 当前阶段：Few-shot Router 或 Cypher Translator。

Selector 是纯确定性逻辑，不调用 LLM，不读取数据库，也不改变 QueryShape 枚举。

### 4.3 QueryShape 固定映射

QueryShape 的明确形状优先于普通场景词：

| QueryShape | Translator 模块 |
| --- | --- |
| UPSTREAM_REACHABILITY | core、anchor_ownership、method_call |
| DIRECT_UPSTREAM | core、anchor_ownership、method_call |
| ORDERED_METHOD_PATH | core、anchor_ownership、method_call、ordered_path |
| FULL_ENTRY_CHAIN | core、anchor_ownership、method_call、entry_api、ordered_path |
| FULL_DOWNSTREAM_CHAIN | core、anchor_ownership、method_call、entry_api、rest、ordered_path |
| DIRECT_REST_EGRESS | core、anchor_ownership、rest |
| GENERAL | core 加场景词和 Few-shot 元数据命中的模块 |

### 4.4 GENERAL 场景识别

GENERAL 不等于未知场景。Selector 对明确业务表达取模块并集：

- REST、远程请求、下游 API、目标服务、跨服务映射 → rest；
- MQ、消息、队列、交换机、发布、消费、路由键 → mq；
- 公开 API、入口 API、上游 API、请求方式、请求体、响应类型 → entry_api；
- 方法调用、直接调用、上游方法、调用者、接口分派、可达 → method_call；
- 有序方法路径、调用顺序、完整入口链、完整下游链 → ordered_path，并补齐对应依赖；
- 类、方法、微服务、归属、声明、实现接口、全限定名 → anchor_ownership；
- 统计、计数、数量、多少、分组、分布 → aggregation；
- 方法修改、变更、影响、回归验证并同时涉及多个独立集合 → decomposition。

多个场景同时命中时取模块并集。未命中业务场景时不回退全量规则，只保留 core 和确实命中的基础模块。

### 4.5 模块依赖

Selector 在初始命中后应用固定依赖：

- method_call、entry_api、rest、mq 和 ordered_path 涉及代码或服务锚点时补充 anchor_ownership；
- ordered_path 补充 method_call；
- FULL_ENTRY_CHAIN 补充 entry_api；
- FULL_DOWNSTREAM_CHAIN 补充 entry_api 和 rest；
- aggregation 不独立推断业务场景，只叠加在已识别的 API、REST、MQ、归属或调用模块上。

依赖闭包完成后按全局固定顺序去重输出。

## 5. 各 Agent 的 Prompt 策略

### 5.1 Primary Agent

Primary Agent 不使用物理 module selector，继续固定加载 primary_agent_semantic_capabilities.md。需要确认该资源完整覆盖：

- 代码元素和服务归属；
- API 与服务交互；
- 方法调用、可达入口和有序路径；
- MQ 发布、路由和消费；
- 分组、计数和返回形状；
- 方法变更题的三集合拆分；
- 完整调用链和完整消息路径不得按列拆分；
- 外部服务在方法变更语境中的明确含义。

Primary Agent 的 system instruction 和 JSON 契约保持稳定。若单一问题无需拆分，解析器继续恢复原始问题；若需要拆分，每个子问题必须重复固定锚点、方向、范围和返回语义。

### 5.2 Few-shot Router

Router 在 Schema 兼容过滤和 QueryShape 过滤后构造 Prompt。其 system prompt 包含：

- 固定的 Router 角色、JSON 输出契约和候选排序优先级；
- 始终存在的 routing core；
- 当前子问题对应的精简 routing profile。

Routing profile 只描述如何区分候选，不包含 MATCH、精确物理关系模式或 Translator 的投影细节。例如：

- method_call 区分直接上游、上游可达和直接下游；
- ordered_path 区分有序路径、完整入口链和完整下游链；
- rest 区分直接 REST 出口、服务级 REST 依赖和跨服务入口映射；
- mq 区分队列消费者、发布方、完整消息路径和调用点；
- aggregation 强调分组维度与聚合值必须同时匹配。

### 5.3 Cypher Translator

DefaultPromptBuilder 在构造 system prompt 时加载 selector 选中的 Translator 模块。Prompt 的其他结构保持不变：

1. system instruction；
2. 选中的代码知识图谱业务规则；
3. 动态 Schema；
4. 可选 Few-shot；
5. 当前子问题；
6. 只输出 Cypher 的要求。

动态 Schema 中的标签、属性和关系方向继续拥有最高优先级。Few-shot 仍不能覆盖 Schema，不能复制示例实体值，也不能拼接多个示例的关系模式。

### 5.4 Question Decomposition 与 Correction

旧 QuestionDecompositionPromptBuilder 改用 Primary Agent 的抽象能力说明，不再注入完整物理规则。其动态 Schema 输入和旧解析契约保持不变，仅作为兼容组件维护。

Cypher Correction 不重新选择规则。它继续复用初次 Translator 生成的 base prompt，因此修正阶段获得完全相同的动态 Schema、Few-shot 和已选业务规则，不会重复注入或扩大模块集合。

## 6. 接口兼容策略

计划新增：

- BusinessRuleModule 枚举；
- 规则模块 loader；
- 确定性的 CodeGraphBusinessRuleSelector；
- 按模块集合渲染规则文本的内部 API。

计划保持不变：

- PromptBuilder.build(schema, question, examples)；
- FewShotRouter.route(question, schema)；
- PrimaryAgent.plan(question)；
- PrimaryAgentPlan 和 PrimaryAgentQuery 字段；
- QueryShape 枚举及现有解析行为；
- Text2CypherPipeline 的阶段顺序；
- Cypher Validator、只读限制和执行链路；
- CLI 与 Text2CypherResponse 输出结构。

DefaultPromptBuilder 和 LLMFewShotRouter 可以增加可选、带默认值的 selector 构造参数，以便单元测试和后续扩展；现有构造方式继续有效。

## 7. 测试计划

### 7.1 Loader 与规则完整性

- 每个模块可从包资源读取且非空；
- 空文件和读取失败抛出 CodeGraphBusinessRulesError；
- 同一模块读取结果被缓存；
- 全量兼容 loader 等于九个模块的固定顺序拼接；
- 现有规则中的关键语义标记在全量拼接结果中全部存在；
- 每条跨场景关键规则只出现在预期模块中。

### 7.2 Selector 单元测试

- 任意 Router/Translator 选择结果均以 core 开头，且 core 只出现一次；
- 普通服务列表只加载 core 和 anchor_ownership；
- REST 问题加载 core、anchor_ownership、rest，不加载 mq、method_call 或 ordered_path；
- MQ 问题加载 core、anchor_ownership、mq，不加载 rest、entry_api 或 ordered_path；
- 直接调用和上游可达加载 method_call，不加载 rest 或 mq；
- 有序方法路径加载 method_call 和 ordered_path；
- 完整入口链加载 method_call、entry_api 和 ordered_path；
- 完整下游链加载 method_call、entry_api、rest 和 ordered_path；
- 聚合 REST 查询在 REST 集合上追加 aggregation；
- 方法变更复合题命中 decomposition，并能同时识别 method_call、entry_api 和 rest；
- 多场景问题取模块并集且顺序稳定；
- 已选 Few-shot 的标签可以补充问题文本遗漏但示例明确表达的场景。

### 7.3 Prompt 集成测试

- Primary Agent 始终包含完整抽象能力，且不包含物理关系模式、MATCH、query_shape 或 requested_fields；
- Router 只包含 routing core 和相关 routing profile，不包含 Cypher 物理规则；
- Translator 的 core 始终且只出现一次；
- REST、MQ、调用链 Prompt 均不包含其他场景的标志性规则；
- 动态 Schema、Few-shot 和用户问题仍位于原有 Prompt 区域；
- Correction Prompt 中所选规则只出现一次；
- 旧 Question Decomposition Prompt 使用抽象能力而非全量物理规则。

### 7.4 回归与质量门禁

实施完成后运行：

- python -m pytest -q；
- python -m ruff check src tests evaluation；
- python -m mypy src evaluation。

修改前基线为 362 passed、16 skipped，Ruff 和 mypy 均通过。真实 Neo4j 和真实 LLM 集成测试继续使用现有环境变量开关，不作为默认单元测试的一部分。

## 8. 实施顺序

1. 建立模块资源目录并逐条迁移现有物理规则。
2. 实现缓存式 loader、枚举、固定顺序渲染和兼容全量 loader。
3. 实现 QueryShape、场景词和 Few-shot 元数据驱动的 selector。
4. 接入 Few-shot Router 和 DefaultPromptBuilder。
5. 将旧 Question Decomposition Prompt 切换到抽象 Primary 能力。
6. 更新原有全量规则 Prompt 测试，并补充 loader、selector 和阶段隔离测试。
7. 运行完整测试和静态检查，修复所有回归。

## 9. 验收标准

- code_graph_business_rules.md 不再作为生产 Prompt 的单体全量资源使用；
- core 在 Router 和 Translator Prompt 中始终且只出现一次；
- REST、MQ、方法调用、入口 API、有序路径和聚合问题只加载所需模块及明确依赖；
- Primary Agent 无需前置分类，仍能独立理解和拆分所有已支持场景；
- Primary Agent 不接收物理图 Schema 或 Cypher 规则；
- 无关业务规则不会进入 Router 或 Translator Prompt；
- 所有现有有效业务规则仍可通过兼容全量 loader 验证；
- Schema 优先级、Few-shot 约束、只读安全和查询行为不变；
- 全部单元测试、Ruff 和 mypy 通过。

## 10. 非目标

本次不包含：

- 新增或修改 QueryShape 类型；
- 修改 Few-shot 示例内容或 Schema 兼容算法；
- 调整 Primary Agent 输出字段；
- 增加 LLM 场景分类调用；
- 修改 Cypher Validator、Neo4j 执行器或只读安全策略；
- 修改 CLI、结果格式或评测 Oracle；
- 为减少 Prompt 长度而删除仍有效的业务规则。
