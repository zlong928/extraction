import React, { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  FileText,
  Layers3,
  Loader2,
  RefreshCw,
  RotateCcw,
  Trash2,
  UploadCloud,
} from "lucide-react";
import {
  API_DISPLAY_URL,
  AuditSummary,
  AuditTable,
  AssetRead,
  ExtractionRead,
  FigureRead,
  LocalArtifactRead,
  PanelRead,
  PaperRead,
  csvUrl,
  deletePaper,
  getPaper,
  getPaperAuditTables,
  getExtraction,
  importLocalArtifact,
  listPaperFigures,
  listLocalArtifacts,
  listPapers,
  retryPaperParse,
  runPaperChartOnly,
  runPaperChartOnlyBatch,
  uploadPaper,
} from "./api";
import {
  AuditProgress,
  FigureBrowser,
  AssetRow,
  ResultPanel,
  StatusBadge,
} from "./components";
import {
  formatDate,
  pipelineDirFromPaper,
  displayAuditPath,
  isMainPipelineRunning,
  busyPaperStatuses,
  busyExtractionStatuses,
  scrollToWorkspace,
} from "./utils";

function App() {
  const [papers, setPapers] = useState<PaperRead[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<PaperRead | null>(null);
  const [figures, setFigures] = useState<FigureRead[]>([]);
  const [selectedAssetId, setSelectedAssetId] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [artifacts, setArtifacts] = useState<LocalArtifactRead[]>([]);
  const [artifactPath, setArtifactPath] = useState("");
  const [artifactTitle, setArtifactTitle] = useState("");
  const [importNotice, setImportNotice] = useState("");
  const [extractions, setExtractions] = useState<Record<number, ExtractionRead>>({});
  const [auditTables, setAuditTables] = useState<Record<string, AuditTable>>({});
  const [auditTableSource, setAuditTableSource] = useState<string | null>(null);
  const [auditTableNotice, setAuditTableNotice] = useState("");
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [importingArtifact, setImportingArtifact] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [selectedPaperIds, setSelectedPaperIds] = useState<Set<number>>(new Set());
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [batchNotice, setBatchNotice] = useState("");

  async function refreshPapers() {
    const next = await listPapers();
    const nextIdSet = new Set(next.map((paper) => paper.id));
    setSelectedPaperIds((current) => {
      const nextSelection = new Set<number>();
      for (const id of current) {
        if (nextIdSet.has(id)) nextSelection.add(id);
      }
      return nextSelection;
    });
    setPapers(next);
    if (!nextIdSet.has(selectedId || -1)) {
      setSelectedId(next[0]?.id || null);
    }
    if (!selectedId && next[0]) setSelectedId(next[0].id);
    return next;
  }

  async function refreshArtifacts() {
    const next = await listLocalArtifacts();
    setArtifacts(next);
    setArtifactPath((current) => current || next[0]?.content_list_path || "");
    return next;
  }

  async function refreshSelected(id: number) {
    const [paper, nextFigures] = await Promise.all([getPaper(id), listPaperFigures(id).catch(() => [])]);
    setSelected(paper);
    setFigures(nextFigures);
    setSelectedAssetId((current) => {
      if (current && paper.assets.some((asset) => asset.id === current)) return current;
      return paper.assets[0]?.id || null;
    });
    setExtractions((current) => {
      const next = { ...current };
      for (const asset of paper.assets) {
        if (asset.latest_extraction) next[asset.id] = asset.latest_extraction;
      }
      return next;
    });
    if (paper.audit_summary) {
      getPaperAuditTables(id)
        .then((payload) => {
          setAuditTables(payload.tables || {});
          setAuditTableSource(payload.source || null);
          const chartRows = payload.tables?.chart_facts?.total || payload.tables?.chart_points?.total || 0;
          const metricRows = payload.tables?.metric_rows?.total || 0;
          setAuditTableNotice(
            chartRows || metricRows
              ? ""
              : "当前链路未产出可信 CSV；历史 fake/test 坐标已被拒绝显示。",
          );
        })
        .catch(() => {
          setAuditTables({});
          setAuditTableSource(null);
          setAuditTableNotice("");
        });
    } else {
      setAuditTables({});
      setAuditTableSource(null);
      setAuditTableNotice("");
    }
  }

  function togglePaperSelection(paperId: number, checked: boolean) {
    setSelectedPaperIds((current) => {
      const next = new Set(current);
      if (checked) next.add(paperId);
      else next.delete(paperId);
      return next;
    });
  }

  async function runChartOnlyBatch() {
    if (!selectedPaperIds.size) {
      setError("请先勾选需要运行数据图提取的论文");
      return;
    }
    setBatchSubmitting(true);
    setBatchNotice("");
    setError("");
    try {
      const result = await runPaperChartOnlyBatch(Array.from(selectedPaperIds));
      await refreshPapers();
      setBatchNotice(
        result.queued
          ? `已提交 ${result.queued} 篇数据图提取任务${result.skipped ? `，跳过 ${result.skipped} 篇` : ""}${result.not_found ? `，未找到 ${result.not_found} 篇` : ""}`
          : "未提交任何数据图提取任务",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量数据图提取提交失败");
    } finally {
      setBatchSubmitting(false);
    }
  }

  function toggleSelectAllPapers() {
    if (!papers.length) return;
    const allIds = papers.map((paper) => paper.id);
    const allSelected = allIds.every((paperId) => selectedPaperIds.has(paperId));
    if (allSelected) {
      setSelectedPaperIds(new Set());
      return;
    }
    setSelectedPaperIds(new Set(allIds));
  }

  useEffect(() => {
    refreshPapers().catch((err) => setError(err.message));
    refreshArtifacts().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      setFigures([]);
      setSelectedAssetId(null);
      return;
    }
    refreshSelected(selectedId).catch((err) => setError(err.message));
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const timer = window.setInterval(() => {
      refreshPapers().catch(() => undefined);
      refreshSelected(selectedId).catch(() => undefined);
    }, busyPaperStatuses.has(selected?.status || "") ? 2500 : 8000);
    return () => window.clearInterval(timer);
  }, [selectedId, selected?.status]);

  useEffect(() => {
    const ids = Object.values(extractions).filter((item) => busyExtractionStatuses.has(item.status)).map((item) => item.id);
    if (!ids.length) return;
    const timer = window.setInterval(async () => {
      const updates = await Promise.all(ids.map((id) => getExtraction(id).catch(() => null)));
      setExtractions((current) => {
        const next = { ...current };
        for (const update of updates) if (update) next[update.asset_id] = update;
        return next;
      });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [extractions]);

  async function submitUpload() {
    if (!file) {
      setError("请选择 PDF 文件");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("这里只接收 PDF");
      return;
    }
    setUploading(true);
    setError("");
    try {
      const paper = await uploadPaper(file, title);
      setFile(null);
      setTitle("");
      await refreshPapers();
      setSelectedId(paper.id);
      scrollToWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function submitArtifactImport() {
    if (!artifactPath) {
      setError("请选择已有 MinerU 产物");
      return;
    }
    setImportingArtifact(true);
    setImportNotice("");
    setError("");
    let importedPaper: PaperRead | null = null;
    try {
      importedPaper = await importLocalArtifact(artifactPath, artifactTitle);
      setImportNotice(`${importedPaper.title} 已导入`);
      setArtifactTitle("");
      setSelectedId(importedPaper.id);
      await refreshPapers();
      await refreshSelected(importedPaper.id).catch(() => undefined);
      const runResult = await runPaperChartOnly(importedPaper.id);
      await refreshPapers();
      await refreshSelected(importedPaper.id).catch(() => undefined);
      const runNotice = runResult.status === "done"
        ? "已同步完成数据图提取"
        : runResult.status === "processing"
        ? "已提交数据图提取任务"
        : `数据图提取状态：${runResult.status}`;
      setImportNotice(`${importedPaper.title} ${runNotice}`);
      scrollToWorkspace();
    } catch (err) {
      const message = err instanceof Error ? err.message : "导入已有产物失败";
      if (message.includes("Redis queue unavailable") && importedPaper) {
        await refreshPapers();
        setSelectedId(importedPaper.id);
        await refreshSelected(importedPaper.id).catch(() => undefined);
        setImportNotice(`${importedPaper.title} Redis 不可用，已尝试同步运行数据图提取`);
        setError("");
        return;
      }
      setError(message);
    } finally {
      setImportingArtifact(false);
    }
  }

  async function retrySelectedPaper() {
    if (!selected) return;
    setError("");
    try {
      const paper = await retryPaperParse(selected.id);
      setSelected(paper);
      await refreshPapers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重试解析失败");
    }
  }

  async function deleteSelectedPaper() {
    if (!selected) return;
    if (!window.confirm(`确定要删除 ${selected.title} 吗？文件和解析结果会一并删除`)) return;

    setDeleting(true);
    setError("");
    try {
      await deletePaper(selected.id);
      const next = await refreshPapers();
      setSelectedId(next[0]?.id || null);
      setSelected(null);
      setFigures([]);
      setSelectedAssetId(null);
      setExtractions({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  async function downloadExtractionCsv(extraction: ExtractionRead) {
    if (!extraction.csv_url) return;
    setError("");
    try {
      const response = await fetch(csvUrl(extraction));
      if (!response.ok) throw new Error(`CSV 下载失败: ${response.status}`);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `extraction-${extraction.id}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV 下载失败");
    }
  }

  const stats = useMemo(() => {
    return {
      total: papers.length,
      done: papers.filter((paper) => paper.status === "done").length,
      running: papers.filter((paper) => busyPaperStatuses.has(paper.status)).length,
      assets: papers.reduce((sum, paper) => sum + paper.asset_count, 0),
    };
  }, [papers]);

  const selectedAsset = selected?.assets.find((asset) => asset.id === selectedAssetId) || selected?.assets[0] || null;
  const selectedExtraction = selectedAsset ? extractions[selectedAsset.id] || selectedAsset.latest_extraction || null : null;
  const selectedOverview = useMemo(() => {
    const assets = selected?.assets || [];
    const ready = assets.filter((asset) => asset.metadata?.extraction_readiness !== "skip").length;
    const skipped = assets.length - ready;
    const finished = assets.filter((asset) => {
      const extraction = extractions[asset.id] || asset.latest_extraction;
      return extraction?.status === "done";
    }).length;
    return { ready, skipped, finished };
  }, [selected?.assets, extractions]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">MinerU Workspace</p>
          <h1>Extraction</h1>
        </div>
        <button className="icon-button" onClick={() => refreshPapers().catch((err) => setError(err.message))} aria-label="刷新">
          <RefreshCw size={18} />
        </button>
      </header>

      {error ? <div className="error"><AlertCircle size={16} />{error}</div> : null}

      <section className="upload-band">
        <label className="dropzone">
          <input type="file" accept=".pdf,application/pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          <UploadCloud size={22} />
          <span>{file ? file.name : "选择 PDF"}</span>
        </label>
        <input className="title-input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="标题，可留空" />
        <button className="primary-button" onClick={submitUpload} disabled={uploading}>
          {uploading ? <Loader2 className="spin" size={17} /> : <UploadCloud size={17} />}
          上传并排队解析
        </button>
      </section>

      <section className="artifact-band">
        <div className="artifact-copy">
          <div className="panel-title inline"><Layers3 size={16} />已有 MinerU 产物</div>
          <span>{artifacts.length ? `${artifacts.length} 个 content_list 可导入` : "未发现本地 content_list"}</span>
        </div>
        <select value={artifactPath} onChange={(event) => setArtifactPath(event.target.value)} disabled={!artifacts.length || importingArtifact}>
          {artifacts.map((artifact) => (
            <option key={artifact.id} value={artifact.content_list_path}>
              {artifact.title} / {artifact.kind} / {artifact.image_count} images
            </option>
          ))}
        </select>
        <input className="title-input" value={artifactTitle} onChange={(event) => setArtifactTitle(event.target.value)} placeholder="导入标题，可留空" />
          <button className="primary-button" onClick={submitArtifactImport} disabled={importingArtifact || !artifactPath}>
            {importingArtifact ? <Loader2 className="spin" size={17} /> : <Layers3 size={17} />}
            {importingArtifact ? "导入并提取数据图" : "Chart-only 数据图提取"}
          </button>
        {importNotice ? <div className="import-notice">{importNotice}</div> : null}
      </section>

      <section className="stats-grid">
        <Metric label="论文" value={stats.total} />
        <Metric label="已就绪" value={stats.done} />
        <Metric label="处理中" value={stats.running} />
        <Metric label="图片资产" value={stats.assets} />
      </section>
      {batchNotice ? <div className="import-notice">{batchNotice}</div> : null}

      <div className="workspace">
        <aside className="paper-list">
          <div className="paper-list-head">
            <div className="panel-title"><FileText size={16} />论文</div>
            <div className="paper-list-actions">
              <button className="secondary-button" onClick={toggleSelectAllPapers} disabled={!papers.length}>
                {papers.every((paper) => selectedPaperIds.has(paper.id)) ? "取消全选" : "全选"}
              </button>
              <button className="secondary-button" onClick={runChartOnlyBatch} disabled={!selectedPaperIds.size || batchSubmitting}>
                {batchSubmitting ? <Loader2 className="spin" size={15} /> : <Layers3 size={15} />}
                批量数据图提取
              </button>
            </div>
          </div>
          {papers.map((paper) => (
            <button key={paper.id} className={`paper-row ${paper.id === selectedId ? "active" : ""}`} onClick={() => setSelectedId(paper.id)}>
              <label
                className="paper-row-check"
                onClick={(event) => {
                  event.stopPropagation();
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedPaperIds.has(paper.id)}
                  onChange={(event) => togglePaperSelection(paper.id, event.target.checked)}
                />
              </label>
                <span className="paper-title">{paper.title}</span>
                <span className="paper-meta"># {paper.id}</span>
                <span className="paper-meta">
                  <StatusBadge status={paper.status} kind="paper" label={isMainPipelineRunning(paper) ? "主链路运行中" : undefined} />
                  {" "}{paper.asset_count} 图 / {paper.figure_count} Fig
                </span>
                <AuditProgress summary={paper.audit_summary} compact />
            </button>
          ))}
          {!papers.length ? <div className="empty">还没有论文</div> : null}
        </aside>

        <section className="detail">
          {selected ? (
            <>
              <div className="detail-head">
                <div>
                  <p className="eyebrow">{selected.original_filename}</p>
                  <h2>{selected.title}</h2>
                  <div className="paper-facts">
                    <span>Paper ID: {selected.id}</span>
                    <span>结果目录: {pipelineDirFromPaper(selected)}</span>
                    {displayAuditPath(selected) ? <span>当前结果: {displayAuditPath(selected)}</span> : null}
                    <span>更新于 {formatDate(selected.updated_at)}</span>
                    <span>{selected.page_count ?? "-"} 页</span>
                    <span>{selected.figure_count} Figures</span>
                    <span>{selected.asset_count} Assets</span>
                  </div>
                </div>
                <div className="detail-actions">
                  <StatusBadge status={selected.status} kind="paper" label={isMainPipelineRunning(selected) ? "主链路运行中" : undefined} />
                  <button className="secondary-button danger" onClick={deleteSelectedPaper} disabled={deleting}>
                    {deleting ? <Loader2 className="spin" size={16} /> : <Trash2 size={16} />}
                    删除文件
                  </button>
                </div>
              </div>

              {selected.error_message ? <div className="error"><AlertCircle size={16} />{selected.error_message}</div> : null}
              {selected.status === "failed" ? (
                <button className="secondary-button retry-inline" onClick={retrySelectedPaper}>
                  <RotateCcw size={16} />
                  重试 MinerU 解析
                </button>
              ) : null}

              <section className="control-strip">
                <div>
                  <div className="panel-title inline"><Layers3 size={16} />MinerU 图片产物</div>
                  <p className="capability-note">展示解析产物、分类结果和已有 CSV 结果。</p>
                </div>
                <div className="run-stats">
                  <Metric label="可检查" value={selectedOverview.ready} />
                  <Metric label="已完成" value={selectedOverview.finished} />
                  <Metric label="跳过" value={selectedOverview.skipped} />
                </div>
                {selected.audit_summary ? <AuditProgress summary={selected.audit_summary} /> : null}
              </section>
              {busyPaperStatuses.has(selected.status) ? (
                <div className="pending-state">
                  <Loader2 className="spin" size={18} />
                  {isMainPipelineRunning(selected)
                    ? "主链路正在运行，已有 MinerU 图片产物可查看。"
                    : "MinerU 正在解析，解析完成后会出现图片资产。"}
                </div>
              ) : null}

              <div className="paper-workbench">
                <section className="figure-browser">
                  <FigureBrowser
                    assets={selected.assets}
                    figures={figures}
                    extractions={extractions}
                    selectedAssetId={selectedAsset?.id || null}
                    auditTables={auditTables}
                    auditTableSource={auditTableSource}
                    auditTableNotice={auditTableNotice}
                    onSelectAsset={setSelectedAssetId}
                    onDownloadCsv={downloadExtractionCsv}
                  />
                </section>
                <ResultPanel
                  asset={selectedAsset}
                  extraction={selectedExtraction}
                  auditTables={auditTables}
                  onDownloadCsv={downloadExtractionCsv}
                />
              </div>
              {selected.status === "done" && !selected.assets.length ? <div className="empty wide">MinerU 没有返回图片资产</div> : null}
            </>
          ) : (
            <div className="empty wide">选择或上传一篇论文</div>
          )}
        </section>
      </div>

      <footer className="footer">API {API_DISPLAY_URL}</footer>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default App;
