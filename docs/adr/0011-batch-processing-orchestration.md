# ADR-011: Batch Processing V1 使用 PostgreSQL 事实与有界调度

- 状态：已采纳
- 日期：2026-07-12

## 背景

批量 PDF 处理需要在 CLI 退出、Redis 通知丢失或 worker 进程退出后保留清单、进度、结果复用和重试历史。Redis 列表不能提供业务事务、关系约束或可重建审计事实。

## 决策

`BatchRun`、`BatchItem`、`BatchEvent`、`PendingJob` 和 `ExtractionRun` 以 PostgreSQL 为事实源。兼容对象的可用性在取得调度行锁前探测；存在才复用、明确不存在才执行，探测异常保持 pending。调度事务随后锁定 BatchRun，按 durable nonterminal Job 数量填充 `batch_concurrency` 窗口；提交 Job、Item 和 Event 后才向 Redis 推送 Job ID。Paper 锁内先检查 active parse attempt：普通 Item 只能在没有 active attempt 时复用成功或传播失败，显式 retry 则必须在等待结束后创建新的 lineage Job。终态事务按 BatchRun、Job、BatchItem、Paper、ExtractionRun 顺序加锁并共同提交 Run、Job、Item、Event 和聚合状态。

模型原始响应和 pipeline outputs 先写入包含 claim generation 与内容摘要的不可覆盖 staging key。对象写入不登记数据库；终态 fencing 校验通过后才在短事务中创建 `StorageObject`/`RunArtifact` 引用。进程在对象写入与数据库登记之间退出只会留下无引用对象，下一次 transport claim 使用新 generation 继续同一 Job/Run，不会与旧字节冲突。V1 依赖对象存储 lifecycle 清理超过安全保留期的 staging orphan，不内置 GC。结果复用还必须验证必需 artifact 的对象字节仍然存在。

V1 部署为单机单 worker 进程，有限并发发生在进程内。周期恢复不接管当前 `hostname:pid` owner 的 processing Job；替代 worker 仅在部署确认旧进程退出后恢复其过期 Job。V1 不承诺多 worker、多主调度、实时 lease 抢占、暂停、公平调度或 processing preemption。

CLI 只调用应用服务并读取 PostgreSQL。`manifest.json` 按 BatchItem ordinal 重建，`events.jsonl` 按创建时间和 ID 重建；两者都是可重复生成的导出，不是写入源。

## 结果

- Redis 故障只影响延迟，不改变已提交 Job 和批次事实。
- Batch 并发、worker 内 LLM 并发和 provider 限流保持独立。
- 显式 retry 创建新 Job/Run lineage，transport recovery 复用原 Job。
- 运维必须保证 singleton worker 切换前旧进程已经退出，并监控周期恢复是否持续运行。
