# P0：基线与评测来源冻结

## 目标

让一次 evaluation 结果能准确说明它评测了哪一版生产代码、由哪一版评测器判定、使用了哪一份数据集，以及当时加载了哪些随包查询知识资源。该阶段不得改变 Text2Cypher 的查询、恢复、安全检查或质量门行为。

## 已确认事实

- `scripts/run-evaluation.ps1` 用目标 revision 的 `src/` 与当前工作区的 `evaluation/` 组合运行。
- `evaluation/run.py` 目前只记录生产 revision、运行配置和 Schema 指纹。
- 默认查询行为还依赖 dataset、Few-shot、QueryShape template、Primary Agent 能力描述和业务规则资源。
- 当前 `error-recovery@35deb94` 没有完整的 40 题正式评测产物，因此元数据改进先用于下一次可复现基线采集。

## 实施

1. 在 `evaluation.run` 中基于实际加载文件计算 SHA-256；记录：
   - 评测器工作区的 Git SHA；
   - `cases.json` 的版本号和字节哈希；
   - 随包资源的逐文件哈希与按路径排序的综合哈希。
2. 资源清单固定覆盖当前影响规划或翻译行为的文件：Few-shot、QueryShape templates、Primary Agent 能力说明，以及业务规则目录中的全部 Markdown 文件。资源不存在或无法读取时评测必须在连接外部服务前失败。
3. Metadata 只记录安全的标识、版本和哈希，不记录 URI、用户名、密码、API key、Prompt、Cypher 或结果行。
4. 报告在标题下展示评测器、数据集和资源综合哈希；保留旧 metadata 的读取兼容，显示 `unknown`。
5. 单元测试用临时文件和固定字节内容验证：数据集版本、哈希稳定性、资源路径排序、内容变化导致综合哈希变化、缺失资源失败，以及报告兼容旧 metadata。

## 验收与回滚

- `pytest`、Ruff、Mypy 通过。
- 既有 `evaluation.run` CLI 参数、质量门和结果判分不变。
- 该阶段只修改评测代码、测试和架构文档，可通过回退本阶段提交恢复。
