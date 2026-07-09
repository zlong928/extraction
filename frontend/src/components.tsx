import React, { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  Download,
  FileText,
  Layers3,
  Loader2,
} from "lucide-react";
import {
  AssetRead,
  AuditSummary,
  AuditTable,
  ExtractionRead,
  FigureRead,
  PanelRead,
  assetUrl,
  csvUrl,
} from "./api";
import {
  CsvPreviewData,
  formatDate,
  extractionStatusText,
  paperStatusText,
  parseCsvPreview,
  tableHeaderLabel,
  canonicalAssetLabel,
  panelTypeFromAuditPanel,
  assetPanelMatches,
  tableForAsset,
  assetLooksChartLike,
  isUsableChartFactRow,
  isSuccessfulDigitizationRow,
  isFailedDigitizationRow,
  tableForPanel,
  panelIdFromMetadata,
  normalizePanelId,
  normalizePanelLetter,
  panelIdFromAuditTables,
  compactAuditTable,
  assetSourceImageNames,
  metadataString,
  normalizeFigureId,
} from "./utils";

export function StatusBadge({ status, kind, label }: { status: string; kind: "paper" | "extraction"; label?: string }) {
  return <span className={`status status-${status}`}>{label || (kind === "paper" ? paperStatusText(status) : extractionStatusText(status))}</span>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function AuditProgress({ summary, compact = false }: { summary: AuditSummary | null; compact?: boolean }) {
  if (!summary) return null;
  const panelTotal = summary.panel_count || summary.processed_panels;
  const percent = Math.max(0, Math.min(100, summary.progress_percent || 0));
  const chartFacts = summary.chart_facts ?? summary.chart_points ?? 0;
  const metricCandidates = summary.metric_candidates ?? 0;
  const benchmarkMetrics = summary.benchmark_metrics ?? summary.metric_rows ?? 0;
  const stateLabel = summary.errors && !benchmarkMetrics && !chartFacts
    ? "链路有错误，未产出表格"
    : benchmarkMetrics
      ? "Benchmark Metrics CSV 已产出"
      : chartFacts
        ? "有图表事实，未映射为 benchmark metric"
        : "仅语义分类";
  const sourceLabel = summary.source === "current_run"
    ? "当前链路"
    : summary.source === "running_events"
      ? "运行中事件"
      : "历史 audit";
  return (
    <div className={compact ? "audit-progress compact-audit" : "audit-progress"}>
      <div className="audit-progress-head">
        <span>{sourceLabel}</span>
        <strong>{panelTotal ? `${summary.processed_panels}/${panelTotal} panels` : "无 panel 统计"}</strong>
      </div>
      <div className="audit-bar" aria-label={`Audit progress ${percent}%`}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="audit-metrics">
        <span>语义 {percent}%</span>
        <span>{chartFacts} facts</span>
        <span>{summary.digitization_results} digitized</span>
        <span>{metricCandidates} candidates</span>
        <span>{benchmarkMetrics} benchmark</span>
        <span>{summary.errors} errors</span>
      </div>
      {!compact ? <p className="audit-state">{stateLabel}</p> : null}
      {!compact && summary.first_error ? <p className="audit-error">{summary.first_error}</p> : null}
    </div>
  );
}

function DataTablePreview({ table }: { table: AuditTable }) {
  return (
    <div className="csv-table-wrap">
      <table>
        <thead>
          <tr>{table.headers.map((header) => <th key={header}>{tableHeaderLabel(header)}</th>)}</tr>
        </thead>
        <tbody>
          {table.rows.slice(0, 20).map((row, rowIndex) => (
            <tr key={rowIndex}>
              {table.headers.map((_, colIndex) => <td key={colIndex}>{row[colIndex] || ""}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeatmapCandidatePreview({ table }: { table: AuditTable }) {
  const valueFor = (row: string[], name: string) => {
    const index = table.headers.indexOf(name);
    return index >= 0 ? row[index] || "" : "";
  };

  return (
    <div className="heatmap-candidate-list">
      {table.rows.slice(0, 12).map((row, rowIndex) => {
        const metric = valueFor(row, "metric_name") || "heatmap candidate";
        const series = valueFor(row, "series");
        const condition = valueFor(row, "condition");
        const value = valueFor(row, "value");
        const valueMin = valueFor(row, "value_min");
        const valueMax = valueFor(row, "value_max");
        const unit = valueFor(row, "unit");
        const scale = valueFor(row, "scale_factor");
        const evidence = valueFor(row, "evidence_type");
        const confidence = valueFor(row, "confidence");
        const review = valueFor(row, "needs_review");
        const range = valueMin || valueMax ? `${valueMin || "?"} - ${valueMax || "?"}` : "";
        return (
          <div className="heatmap-candidate-row" key={`${metric}-${rowIndex}`}>
            <div className="heatmap-candidate-title">
              <strong>{metric}</strong>
              {confidence ? <span>{confidence}</span> : null}
            </div>
            <div className="heatmap-candidate-fields">
              {series ? <span><b>series</b>{series}</span> : null}
              {condition ? <span><b>condition</b>{condition}</span> : null}
              {value ? <span><b>value</b>{value}</span> : null}
              {range ? <span><b>range</b>{range}</span> : null}
              {unit ? <span><b>unit</b>{unit}</span> : null}
              {scale ? <span><b>scale</b>{scale}</span> : null}
              {evidence ? <span><b>evidence</b>{evidence}</span> : null}
              {review ? <span><b>review</b>{review}</span> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function FigureBrowser({
  assets,
  figures,
  extractions,
  selectedAssetId,
  auditTables,
  auditTableSource,
  auditTableNotice,
  onSelectAsset,
  onDownloadCsv,
}: {
  assets: AssetRead[];
  figures: FigureRead[];
  extractions: Record<number, ExtractionRead>;
  selectedAssetId: number | null;
  auditTables: Record<string, AuditTable>;
  auditTableSource: string | null;
  auditTableNotice: string;
  onSelectAsset: (assetId: number) => void;
  onDownloadCsv: (extraction: ExtractionRead) => void;
}) {
  const assetsById = new Map(assets.map((asset) => [asset.id, asset]));
  const figureSections = figures
    .map((figure) => ({
      figure,
      assets: figure.assets.map((assetId) => assetsById.get(assetId)).filter(Boolean) as AssetRead[],
    }))
    .filter((section) => section.assets.length || section.figure.panels.length);
  const groupedAssetIds = new Set(figureSections.flatMap((section) => section.assets.map((asset) => asset.id)));
  const ungrouped = assets.filter((asset) => !groupedAssetIds.has(asset.id));

  if (!assets.length) {
    return <div className="empty wide">等待解析完成后显示图片资产</div>;
  }

  return (
    <div className="figure-stack">
      {figureSections.map(({ figure, assets: figureAssets }) => (
        <FigureSection
          key={figure.id}
          figure={figure}
          assets={figureAssets}
          extractions={extractions}
          selectedAssetId={selectedAssetId}
          auditTables={auditTables}
          auditTableSource={auditTableSource}
          auditTableNotice={auditTableNotice}
          onSelectAsset={onSelectAsset}
          onDownloadCsv={onDownloadCsv}
        />
      ))}
      {ungrouped.length ? (
        <section className="figure-section">
          <div className="figure-head">
            <div>
              <h3>未归组资产</h3>
            </div>
            <span className="figure-count">{ungrouped.length} assets</span>
          </div>
          <div className="asset-list">
            {ungrouped.map((asset) => (
              <AssetRow
                key={asset.id}
                asset={asset}
                extraction={extractions[asset.id] || asset.latest_extraction || undefined}
                selected={asset.id === selectedAssetId}
                auditTables={auditTables}
                auditTableSource={auditTableSource}
                auditTableNotice={auditTableNotice}
                onSelect={() => onSelectAsset(asset.id)}
                onDownloadCsv={onDownloadCsv}
              />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function FigureSection({
  figure,
  assets,
  extractions,
  selectedAssetId,
  auditTables,
  auditTableSource,
  auditTableNotice,
  onSelectAsset,
  onDownloadCsv,
}: {
  figure: FigureRead;
  assets: AssetRead[];
  extractions: Record<number, ExtractionRead>;
  selectedAssetId: number | null;
  auditTables: Record<string, AuditTable>;
  auditTableSource: string | null;
  auditTableNotice: string;
  onSelectAsset: (assetId: number) => void;
  onDownloadCsv: (extraction: ExtractionRead) => void;
}) {
  return (
    <section className="figure-section">
      <div className="figure-head">
        <div>
          <h3>{figure.figure_id}</h3>
        </div>
        <span className="figure-count">{assets.length} assets</span>
      </div>
      {figure.panels.length ? <PanelStrip panels={figure.panels} assets={assets} auditTables={auditTables} /> : null}
      <div className="asset-list">
        {assets.map((asset) => (
          <AssetRow
            key={asset.id}
            asset={asset}
            extraction={extractions[asset.id] || asset.latest_extraction || undefined}
            selected={asset.id === selectedAssetId}
            auditTables={auditTables}
            auditTableSource={auditTableSource}
            auditTableNotice={auditTableNotice}
            onSelect={() => onSelectAsset(asset.id)}
            onDownloadCsv={onDownloadCsv}
          />
        ))}
      </div>
    </section>
  );
}

function PanelStrip({ panels, assets, auditTables }: { panels: PanelRead[]; assets: AssetRead[]; auditTables: Record<string, AuditTable> }) {
  return (
    <div className="panel-strip">
      {panels.map((panel) => {
        const auditPanelType = panelTypeFromAuditPanel(panel, assets, auditTables);
        return (
          <span key={panel.id} title={`${panel.domain_task} / ${panel.extractor}`}>
            {panel.panel_id}
            <small>{auditPanelType || panel.panel_type || "unusable"}</small>
          </span>
        );
      })}
    </div>
  );
}

export function AssetRow({
  asset,
  extraction,
  selected,
  auditTables,
  auditTableSource,
  auditTableNotice,
  onSelect,
  onDownloadCsv,
}: {
  asset: AssetRead;
  extraction?: ExtractionRead;
  selected: boolean;
  auditTables: Record<string, AuditTable>;
  auditTableSource: string | null;
  auditTableNotice: string;
  onSelect: () => void;
  onDownloadCsv: (extraction: ExtractionRead) => void;
}) {
  const [zoomOpen, setZoomOpen] = useState(false);
  const assetLabel = canonicalAssetLabel(asset, auditTables);
  return (
    <article className={`asset-row ${selected ? "selected" : ""}`} onClick={onSelect}>
      <div className="asset-visual-stack">
        <div className="asset-source-id">{assetLabel}</div>
        <button
          className="asset-image-wrap"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            setZoomOpen(true);
          }}
          aria-label="放大图片"
        >
          <img src={assetUrl(asset)} alt={assetLabel} />
        </button>
        <AssetAuditTables asset={asset} tables={auditTables} source={auditTableSource} notice={auditTableNotice} />
      </div>
      {extraction?.csv_url || extraction ? (
        <div className="asset-body minimal-asset-body">
          {extraction?.csv_url ? (
            <button className="download-link" type="button" onClick={(event) => { event.stopPropagation(); onDownloadCsv(extraction); }}>
              <Download size={15} />
              CSV
            </button>
          ) : null}
          {extraction ? <ExtractionSummary extraction={extraction} /> : null}
        </div>
      ) : null}
      {zoomOpen ? (
        <div
          className="image-zoom-backdrop"
          role="dialog"
          aria-modal="true"
          onClick={(event) => {
            event.stopPropagation();
            setZoomOpen(false);
          }}
        >
          <button className="image-zoom-close" type="button" onClick={() => setZoomOpen(false)}>关闭</button>
          <img src={assetUrl(asset)} alt={assetLabel} />
        </div>
      ) : null}
    </article>
  );
}

function AssetAuditTables({
  asset,
  tables,
  source,
  notice,
}: {
  asset: AssetRead;
  tables: Record<string, AuditTable>;
  source: string | null;
  notice: string;
}) {
  const ordered = [
    ["chart_facts", "Chart Facts CSV 预览"],
    ["heatmap_candidates", "Heatmap Candidates 预览"],
    ["metric_rows", "Benchmark Metrics CSV 预览"],
  ] as const;
  const visible: { key: string; label: string; table: AuditTable }[] = [];
  for (const [key, label] of ordered) {
    const table = tableForAsset(tables[key] || (key === "chart_facts" ? tables.chart_points : undefined), asset);
    if (table) visible.push({ key, label, table });
  }

  const sourceLabel = source === "current_run" ? "当前链路" : source === "historical_audit" ? "历史 audit" : "结果";
  if (!visible.length) {
    if (!notice || !assetLooksChartLike(asset)) return null;
    return (
      <section className="asset-audit-tables audit-empty" onClick={(event) => event.stopPropagation()}>
        <div className="panel-title inline"><BarChart3 size={16} />CSV 预览</div>
        <p>{notice}</p>
      </section>
    );
  }

  return (
    <section className="asset-audit-tables" onClick={(event) => event.stopPropagation()}>
      <div className="panel-title inline"><BarChart3 size={16} />{sourceLabel} 源图结果预览</div>
      {visible.some((item) => item.key === "chart_facts") && !visible.some((item) => item.key === "metric_rows") ? (
        <p className="audit-state">有图表事实，未映射为 benchmark metric</p>
      ) : null}
      {visible.map(({ key, label, table }) => (
        <div className="asset-audit-card" key={key}>
          <div className="csv-head">
            <span>{label}</span>
            <strong>{table.total} rows</strong>
          </div>
          {key === "heatmap_candidates" ? <HeatmapCandidatePreview table={table} /> : <DataTablePreview table={table} />}
        </div>
      ))}
    </section>
  );
}

export function ResultPanel({
  asset,
  extraction,
  auditTables,
  onDownloadCsv,
}: {
  asset: AssetRead | null;
  extraction: ExtractionRead | null;
  auditTables: Record<string, AuditTable>;
  onDownloadCsv: (extraction: ExtractionRead) => void;
}) {
  if (!asset) {
    return (
      <aside className="result-panel">
        <div className="empty wide">选择一张图片查看详情</div>
      </aside>
    );
  }

  const assetLabel = canonicalAssetLabel(asset, auditTables);
  return (
    <aside className="result-panel">
      <div className="result-head">
        <div>
          <p className="eyebrow">Asset #{asset.id}</p>
          <h3>{assetLabel}</h3>
        </div>
        {extraction ? <StatusBadge status={extraction.status} kind="extraction" /> : null}
      </div>
      <div className="preview-frame">
        <img src={assetUrl(asset)} alt={assetLabel} />
      </div>
      <div className="result-actions">
        {extraction?.csv_url ? (
          <button className="download-link" type="button" onClick={() => onDownloadCsv(extraction)}>
            <Download size={15} />
            下载 CSV
          </button>
        ) : null}
      </div>
      {extraction ? <ExtractionDetail extraction={extraction} /> : <AssetMetadata asset={asset} canonicalLabel={assetLabel} />}
    </aside>
  );
}

function AssetMetadata({ asset, canonicalLabel }: { asset: AssetRead; canonicalLabel: string }) {
  const metadata = asset.metadata || {};
  const rows: [string, unknown][] = [
    ["源图 ID", canonicalLabel],
    ["页面", asset.page_number ?? "-"],
    ["尺寸", asset.width && asset.height ? `${asset.width} x ${asset.height}` : "-"],
    ["类型", asset.asset_type],
    ["MinerU type", metadata.mineru_type || "-"],
    ["Readiness", metadata.extraction_readiness || "-"],
    ["Skip reason", metadata.skip_reason || "-"],
  ];
  return (
    <div className="kv-list">
      {rows.map(([label, value]) => (
        <div key={String(label)}>
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function ExtractionDetail({ extraction }: { extraction: ExtractionRead }) {
  const result = extraction.result || {};
  const metricRows = Array.isArray(result.metric_rows) ? result.metric_rows : [];
  const observations = Array.isArray(result.image_observations) ? result.image_observations : [];
  const rowCount = typeof result.row_count === "number" ? result.row_count : metricRows.length;
  return (
    <div className="extraction-detail">
      <div className="kv-list">
        <div>
          <span>创建时间</span>
          <strong>{formatDate(extraction.created_at)}</strong>
        </div>
        <div>
          <span>完成时间</span>
          <strong>{extraction.completed_at ? formatDate(extraction.completed_at) : "-"}</strong>
        </div>
        <div>
          <span>Benchmark metric rows</span>
          <strong>{rowCount}</strong>
        </div>
      </div>
      {extraction.error_message ? <div className="error inline-error"><AlertCircle size={16} />{extraction.error_message}</div> : null}
      <AgentTrace result={extraction.result} />
      {metricRows.length ? <JsonPreview title="Benchmark metric rows" icon={<BarChart3 size={15} />} value={metricRows.slice(0, 5)} /> : null}
      {observations.length ? <JsonPreview title="Image observations" icon={<Layers3 size={15} />} value={observations.slice(0, 5)} /> : null}
      {extraction.csv_url ? <CsvTablePreview extraction={extraction} /> : null}
      {!metricRows.length && !observations.length ? <JsonPreview title="Raw result" icon={<FileText size={15} />} value={result} /> : null}
    </div>
  );
}

function ExtractionSummary({ extraction }: { extraction: ExtractionRead }) {
  const rowCount = typeof extraction.result?.row_count === "number" ? extraction.result.row_count : null;
  const imageType = typeof extraction.result?.image_type === "string" ? extraction.result.image_type : "";
  return (
    <div className="extraction-summary">
      <StatusBadge status={extraction.status} kind="extraction" />
      {imageType ? <span>{imageType}</span> : null}
      {rowCount !== null ? <span>{rowCount} 行</span> : null}
      {extraction.error_message ? <p>{extraction.error_message}</p> : null}
      <AgentTrace result={extraction.result} />
    </div>
  );
}

function AgentTrace({ result }: { result: Record<string, unknown> | null }) {
  const trace = Array.isArray(result?.agent_trace) ? result.agent_trace : [];
  if (!trace.length) return null;
  return (
    <div className="agent-trace">
      {trace.map((step, index) => {
        const item = step as Record<string, unknown>;
        const label = String(item.phase || `STEP_${index + 1}`);
        const detail = [item.image_type, item.route_family, item.chart_type, item.review_status].filter(Boolean).join(" / ");
        return (
          <span key={`${label}-${index}`}>
            {label.replaceAll("_", " ")}
            {detail ? `: ${detail}` : ""}
          </span>
        );
      })}
    </div>
  );
}

function JsonPreview({ title, icon, value }: { title: string; icon: React.ReactNode; value: unknown }) {
  return (
    <details className="json-preview" open>
      <summary>{icon}{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function CsvTablePreview({ extraction }: { extraction: ExtractionRead }) {
  const [preview, setPreview] = useState<CsvPreviewData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setPreview(null);
    setError("");
    fetch(csvUrl(extraction))
      .then((response) => {
        if (!response.ok) throw new Error(`CSV ${response.status}`);
        return response.text();
      })
      .then((text) => {
        if (!cancelled) setPreview(parseCsvPreview(text));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "CSV 读取失败");
      });
    return () => {
      cancelled = true;
    };
  }, [extraction.id, extraction.csv_url]);

  if (error) {
    return <div className="csv-preview error inline-error"><AlertCircle size={16} />{error}</div>;
  }
  if (!preview) {
    return <div className="csv-preview loading"><Loader2 className="spin" size={16} />CSV 预览加载中</div>;
  }
  if (!preview.headers.length) {
    return <div className="csv-preview loading">CSV 无可预览行</div>;
  }

  return (
    <section className="csv-preview">
      <div className="csv-head">
        <span>Benchmark Metrics CSV 预览</span>
        <strong>{preview.rows.length} rows shown</strong>
      </div>
      <div className="csv-table-wrap">
        <table>
          <thead>
            <tr>
              {preview.headers.map((header, index) => <th key={`${header}-${index}`}>{header || `Column ${index + 1}`}</th>)}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {preview.headers.map((_, colIndex) => <td key={colIndex}>{row[colIndex] || ""}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
