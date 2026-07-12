# ADR-010: DuckDB 与 Parquet 作为冻结交付格式

- 状态：已采纳
- 日期：2026-07-11

## 背景

下游分析需要可复制、可查询、可长期保存的数据包，但 DuckDB 单文件和 Parquet 文件不适合承担并发任务状态、关系约束与在线事务。

## 决策

在线事实仍以 PostgreSQL 为准。独立 `DeliveryBuilder` 在固定 `snapshot_at`、经过校验的项目/论文/状态范围和配置下，以 PostgreSQL `REPEATABLE READ` 事务读取终态运行及规范化结果，按稳定顺序生成：

- 每张逻辑表一个 Parquet 文件；
- 包含同等表的 DuckDB；
- 面向人工的 Excel 与 Markdown；
- 带版本、范围、schema/pipeline/model/prompt、记录数和逐文件 SHA-256 的 `manifest.json`。

交付版本名唯一，发布后不可原地修改；重建或修订必须使用新版本。

## 结果

- Parquet 适合列式交换和湖仓工具，DuckDB 适合单文件离线查询。
- 同一快照的行集合、排序和配置哈希可复核；Excel zip 时间戳被规范化。
- 格式库升级可能改变二进制编码，因此“等价”以 manifest 范围、记录内容和校验策略为准，不承诺跨依赖版本逐字节相同。
