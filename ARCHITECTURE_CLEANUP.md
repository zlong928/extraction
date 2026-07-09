# 架构冗余与清理规范（修正版）

> 生成日期: 2026-07-06
> 分析范围: 全项目代码库

---

## 实际架构关系

```
┌──────────────────────────────────────────────────────────────────┐
│  content_pipeline/  ←── 主管线（自包含的核心提取逻辑）              │
│  ├── contracts/     (协议/数据模型)                                │
│  ├── graph/         (文档/图表/布局图谱)                            │
│  ├── evidence/      (证据包构建)                                   │
│  ├── llm/           (LLM 阶段调度) ←─ 动态 import app 的 LLM 客户端 │
│  ├── visual/        (质量门控/热图/上下文)                          │
│  ├── mineru/        (内容块规范化)                                  │
│  ├── orchestration/ (管线编排器)                                    │
│  └── cli_bridge.py  (CLI 入口)                                     │
│                                                                  │
│  app/  ←── FastAPI Web 包装层（调用 content_pipeline）              │
│  ├── api/           (HTTP 端点)                                    │
│  ├── models/        (SQLAlchemy ORM 模型)                         │
│  ├── services/pdf/  (上传/解析/管线调度 → 调用 content_pipeline)    │
│  ├── services/agent/ (LLM 客户端 —— 被 content_pipeline 动态 import)│
│  ├── services/mineru_* (MinerU 解析/资产构建 —— 解析 PDF 的入口)    │
│  ├── services/image_extraction.py  ── 旧提取管线                    │
│  └── services/audit_table_service.py                              │
└──────────────────────────────────────────────────────────────────┘
```

**关键依赖关系:**
- `content_pipeline/` **不静态 import `app/`**（纯自包含管线）
- `content_pipeline/llm/client.py` 在运行时 `importlib.import_module("app.services.agent.llm_client")` 动态加载 LLM 客户端
- `app/services/pdf/pipeline.py` import `content_pipeline` 调用管线
- `app/services/mineru_*.py` import `content_pipeline.mineru.image_path_resolver` 复用图片路径解析

---

## 1. 🔴 已废弃的旧提取管线（被 content_pipeline 取代）

`content_pipeline/__init__.py` 明确声明:
> "This package is the new MinerU content-list-first pipeline. It does not depend on the legacy image-first extraction path."

### `app/services/image_extraction.py` — 633 行旧管线

| 类/方法 | 行数 | 说明 |
|---------|------|------|
| `PromptSchemaModelClient` | 1–81 | 旧的 LLM prompt 调用封装 |
| `ImageExtractionService` | 83–633 | 完整旧提取生命周期（create/run/retry） |
| 含 20+ 私有方法 | — | prompt 调用、CSV 写入、提取结果处理 |
| 测试: `tests/test_paper_upload_and_image_extraction.py` | 310 行 | 但仍在使用旧管线 |

**引用链:**
```
app/api/extractions.py  →  ImageExtractionService  (全部接口: GET/POST)
app/api/papers.py       →  ImageExtractionService  (部分接口)
```

**状态:** 虽代码仍被 API 引用，但 `content_pipeline` 是替代方案。如果新管线 API 未暴露这些旧端点的等位接口，则旧代码仍然必要。**如已迁移，则此文件全线废弃。**

### `app/api/extractions.py` — 42 行

```python
router = APIRouter(prefix="/extractions")
# GET /{id}         → 获取旧提取结果
# GET /{id}/csv     → 下载旧 CSV
# POST /{id}/retry  → 重试旧提取
```

**状态:** 纯服务于 `ImageExtraction` 模型 + `ImageExtractionService`。旧管线若废弃则此文件也废弃。

### `app/services/audit_table_service.py` — 212 行

| 函数 | 说明 |
|------|------|
| `chart_fact_records()` | 从提取结果中提取图表事实记录 |
| `panel_image_map()` | 构建 panel_id → 图片路径映射 |
| `rows_from_records()` | 格式化为前端表格数据 |

**引用:** 仅在 `app/api/papers.py` 中被调用（前端审计表查看）。

**状态:** 如果前端审计表功能来自 `content_pipeline/export/audit_exporter.py` 而非此文件，则废弃。

---

## 2. 🟡 `app/` 内部真实重复

### 2.1 `mineru_asset_builder.py`: `ingest()` vs `build_context_for_image()` 内部重复 ~70%

| 方法 | 行数 | 职责 |
|------|------|------|
| `ingest()` | L36–251 (216行) | 写 DB / copy 文件 / 建 Figure/Panel 记录 |
| `build_context_for_image()` | L865–1083 (219行) | 返回 dict 给 content_pipeline |

**重复逻辑 (~300 行共享):**
- 解析 content_list 提取 image/chart block
- 按文件名匹配图片 (`image_name in item_path`)
- 构建 captions、nearby_content、section_hierarchy、citation_context
- 调用 `LocalImageProfiler.profile()` 生成 profile
- 构建 panel_context
- 构建 metadata 字典（30+ 字段几乎完全一致）

**建议:** 提取为 `_build_image_metadata()` 私有方法，两个入口仅保留差异部分（~30 行）。

### 2.2 `compact_text()` 重复定义

| 文件 | 行数 | 实现 |
|------|------|------|
| `app/core/constants.py` L10–L12 | 3 | `str(value).replace("\n"," ").strip()` + `" ".join(split())` |
| `app/services/mineru_asset_builder_panel_context.py` L7–L9 | 3 | **完全相同** |

**建议:** 统一到 `app.core.constants.compact_text`，panel_context 中删除重复定义（该文件已 import 同模块的 `MARKDOWN_IMAGE_RE`）。

### 2.3 `pdf_service.py` — 纯透传层

```
app/services/pdf_service.py:  29 行，仅 from ... import 再 __all__ 导出
```

**调用方:**
```
app/schemas.py    L9:  from app.services.pdf_service import audit_summary_for_paper
app/worker.py     L11: from app.services.pdf_service import ChartOnlyRunAlreadyActive, ...
```

**建议:** 调用方改为直接 `from app.services.pdf import ...`，删除 `pdf_service.py`。

### 2.4 `mineru_asset_builder.py` 中转发类方法

```python
@staticmethod
def _resolve_image(root, image_path): return resolve_image(root, image_path)

@classmethod
def find_extract_dir(cls, image_path): return find_extract_dir(image_path)

@classmethod
def markdown_image_paths(cls, ...): return markdown_image_paths(...)

@classmethod
def content_list_image_paths(cls, ...): return content_list_image_paths(...)
```

**建议:** 外部调用方直接调用 `app.services.mineru_asset_builder_paths` 模块函数，删除这些转发方法。

---

## 3. 🟡 过时文件

| 文件 | 说明 | 建议 |
|------|------|------|
| `scripts/experiments/run_fig3l_point_smoke_test.py` | 实验性单图烟雾测试 | 移到 `scripts/archive/` |
| `docs/BENCHMARK_ONTOLOGY_V0.md` | V0 已过时，V1 存在 | 删除 |
| `docs/plans/` 目录 | 规划文档，内容已实现 | 归档到 `docs/archive/` |
| `.env.backup_invalid_centos_20260703160725` | 环境变量备份含敏感信息 | 删除 |
| `.env.backup_newapi_20260703131627` | 同上 | 删除 |
| `pdf/*.pdf` | 6 篇 PDF 论文源文件 (~27MB) | 移出 git 跟踪 |

---

## 4. 🟢 `.gitignore` 缺失项

当前 `.gitignore` 未覆盖以下运行产物：

```
# 数据库
data/extraction.db

# 运行时数据
data/uploads/
data/results/
data/runtime/
data/pipeline_batch/
data/content_pipeline_results/
data/mineru_batch_summary.jsonl
data/pipeline_batch_summary.jsonl

# 构建缓存
frontend/tsconfig.tsbuildinfo

# 大二进制文件
pdf/*.pdf

# 测试报告
htmlcov/
```

---

## 5. ✅ 健康代码（无问题）

以下 `app/` 代码是活跃的桥梁/基础设施，**不应清理**：

| 文件 | 理由 |
|------|------|
| `app/main.py` | FastAPI 入口 |
| `app/config.py` | 全局配置 |
| `app/db.py` | 数据库连接/迁移 |
| `app/schemas.py` | Pydantic 响应模型 |
| `app/models/` 全部 | SQLAlchemy ORM 模型 |
| `app/api/papers.py` | 论文上传/列表/删除 API |
| `app/services/pdf/*` | 上传 → 解析 → 管线调度桥梁 |
| `app/services/mineru_parser.py` | MinerU API 客户端（PDF 解析入口） |
| `app/services/mineru_asset_builder.py` (核心逻辑) | 资产构建（去除内部重复后） |
| `app/services/mineru_asset_builder_paths.py` | 路径解析 |
| `app/services/mineru_asset_builder_panel_context.py` (核心逻辑) | Panel 上下文（去除 compact_text 重复后） |
| `app/services/storage.py` | 文件存储 |
| `app/services/local_image_profiler.py` | 图片确定性画像 |
| `app/services/agent/llm_client.py` | LLM 客户端（被 content_pipeline 动态 import） |
| `app/services/extraction/llm_config.py` | LLM 配置（被 content_pipeline 动态 import） |
| `app/services/agent/types.py` | LLM 客户端类型 |
| `app/services/document_parser.py` | 文档解析模型 |
| `app/queue/redis_queue.py` | Redis 队列 |
| `app/worker.py` | 后台 worker |

---

## 6. 清理优先级

### 🔴 P0 — 立即（安全）

| # | 操作 | 说明 |
|---|------|------|
| 1 | 删除 `.env.backup_*` | 可能包含 API 密钥 |
| 2 | 更新 `.gitignore` | 防止数据库/日志再次提交 |
| 3 | 清除已跟踪的 `data/*` 运行时文件 | `git rm --cached` |

### 🟡 P1 — 代码质量

| # | 操作 | 文件 | 风险 |
|---|------|------|------|
| 4 | 提取共享逻辑，消除 `ingest()` / `build_context_for_image()` 间 ~70% 重复 | `mineru_asset_builder.py` | 中 |
| 5 | 删除重复 `compact_text()` 定义 | `mineru_asset_builder_panel_context.py` | 低 |
| 6 | 删除 `pdf_service.py` 透传层，调用方直接 import `app.services.pdf` | `pdf_service.py` + `schemas.py` `worker.py` | 低 |
| 7 | 删除 `mineru_asset_builder.py` 中的转发类方法 | `mineru_asset_builder.py` | 低 |

### 🟢 P2 — 评估后清理

| # | 操作 | 评估条件 |
|---|------|----------|
| 8 | 删除 `app/services/image_extraction.py` | content_pipeline 是否已提供等位 API？ |
| 9 | 删除 `app/api/extractions.py` | 旧提取 API 是否仍需要？ |
| 10 | 删除 `scripts/experiments/` | 实验是否完成？ |
| 11 | 删除 `docs/BENCHMARK_ONTOLOGY_V0.md` | V1 是否已覆盖全部内容？ |
| 12 | 归档 `docs/plans/` | 所有规划是否已实现？ |
| 13 | `pdf/*.pdf` 移出 git 跟踪 | 确认无硬编码路径引用 |

---

## 附录: 文件依赖关系速查

```
app/ → content_pipeline:
  app/services/pdf/pipeline.py          → content_pipeline (run_content_pipeline)
  app/services/mineru_*.py              → content_pipeline.mineru.image_path_resolver

content_pipeline → app (运行时动态):
  content_pipeline/llm/client.py        → importlib: app.services.agent.llm_client
  content_pipeline/llm/client.py        → importlib: app.services.extraction.llm_config

content_pipeline ↛ app (静态): 0 个静态 import

app/ 内部调用链:
  app/api/papers.py         → pdf_service, image_extraction, audit_table_service, storage
  app/api/extractions.py    → image_extraction, storage
  app/services/pdf/parse.py → mineru_parser, mineru_asset_builder, storage, pdf/pipeline
  app/services/pdf/pipeline → agent/llm_client, extraction/llm_config, content_pipeline
  app/services/image_extraction.py → agent/llm_client, extraction/llm_config, storage
```
