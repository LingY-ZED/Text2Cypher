# 代码知识图谱业务语义：聚合

- 服务级 REST 调用数量统一对 `下游调用` 关系计数，并投影 `count(DISTINCT remote) AS 调用关系数`。
