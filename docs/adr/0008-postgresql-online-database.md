# ADR-008: PostgreSQL 作为在线业务数据库

- 状态：已采纳
- 日期：2026-07-11

## 背景

系统需要处理数万文献、多个并发 worker、重复提交、重试和不可变运行历史。SQLite 的单文件写锁、宿主机文件锁和共享卷假设不能提供跨主机事务并发与行级 claim。

## 决策

生产在线数据库使用 PostgreSQL；SQLite 仅保留为本地开发、单进程测试和旧数据迁移源。所有结构变化由 Alembic 管理。任务用唯一幂等键去重，worker 使用行锁、可续租 lease 和单调递增的 claim generation fencing；论文级互斥在 PostgreSQL 上使用 advisory lock。数据库 trigger 兜底保护终态运行、运行子事实、对象元数据和已发布交付，避免 Core/bulk SQL 绕过 ORM 不可变规则。业务大对象不得进入数据库。

## 结果

- `projects`、`papers`、`storage_objects`、`pending_jobs`、`extraction_runs`、`structured_results`、`delivery_versions` 形成在线事实链。
- `ExtractionRun` 的终态事实不可更新或删除；重试创建新任务和新运行。
- 部署必须先执行 `alembic upgrade head`，并对 PostgreSQL 做备份、监控和恢复演练。
- SQLite 新增列因方言限制不具备所有生产外键，但不可变 trigger、任务 fencing 与活动论文哈希唯一性在两种方言上均受 migration 管理；生产关系约束以 PostgreSQL migration 结果为准。
