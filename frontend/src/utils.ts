import { AssetRead, AuditTable, PanelRead } from "./api";

export function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function paperStatusText(status: string) {
  const labels: Record<string, string> = {
    pending: "等待 MinerU",
    processing: "MinerU 解析中",
    done: "已解析",
    failed: "解析失败",
  };
  return labels[status] || status;
}

export function extractionStatusText(status: string) {
  const labels: Record<string, string> = {
    pending: "等待处理",
    processing: "处理中",
    done: "已完成",
    failed: "失败",
    needs_review: "需复核",
    skipped: "已跳过",
  };
  return labels[status] || status;
}

export function pipelineDirFromPaper(paper: { id: number }) {
  return `data/content_pipeline_results/paper_${paper.id}`;
}

export function displayAuditPath(paper: { audit_summary?: { audit_path?: string } | null } | null) {
  if (!paper?.audit_summary?.audit_path) return null;
  return String(paper.audit_summary.audit_path).replace(/[/\\]extraction_audit\.json$/, "");
}

export function isMainPipelineRunning(paper: { status: string; asset_count: number; audit_summary?: { source?: string } | null } | null | undefined) {
  return paper?.status === "processing" && (paper.asset_count > 0 || paper.audit_summary?.source === "running_events");
}

export function scrollToWorkspace() {
  window.requestAnimationFrame(() => {
    document.querySelector(".workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

export const busyPaperStatuses = new Set(["pending", "processing"]);
export const busyExtractionStatuses = new Set(["pending", "processing"]);

export type CsvPreviewData = {
  headers: string[];
  rows: string[][];
};

export function parseCsvPreview(text: string): CsvPreviewData {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (quoted) {
      if (char === '"' && next === '"') {
        cell += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
      continue;
    }
    if (char === '"') {
      quoted = true;
      continue;
    }
    if (char === ",") {
      row.push(cell);
      cell = "";
      continue;
    }
    if (char === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    if (char !== "\r") cell += char;
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }

  const [headers = [], ...body] = rows.filter((item) => item.some((value) => value.trim()));
  return {
    headers: headers.slice(0, 12),
    rows: body.slice(0, 20).map((item) => item.slice(0, 12)),
  };
}

export function tableHeaderLabel(header: string) {
  const labels: Record<string, string> = {
    series_name: "series",
    point_index: "#",
    x_axis_label: "x axis",
    x_value: "x",
    x_unit: "x unit",
    y_axis_label: "y axis",
    y_value: "y",
    y_unit: "y unit",
    z_label: "z axis",
    z_value: "z",
    z_unit: "z unit",
    scale_factor: "scale",
    category_label: "region",
    evidence_type: "evidence",
    digitization_status: "status",
    needs_review: "review",
    source_phase: "phase",
    review_status: "review",
    metric_name: "metric",
    value_min: "min",
    value_max: "max",
    condition: "condition",
    extraction_source: "source",
  };
  return labels[header] || header;
}

export function isUsableChartFactRow(row: string[], headers: string[]) {
  const getValue = (name: string) => {
    const index = headers.indexOf(name);
    return index >= 0 ? String(row[index] || "").trim().toLowerCase() : "";
  };

  const pointIndex = getValue("point_index");
  if (!pointIndex || pointIndex === "axis") return false;

  const digitizationStatus = getValue("digitization_status");
  if (digitizationStatus === "no_chart_detected" || digitizationStatus === "failed") return false;

  const xValue = getValue("x_value");
  const yValue = getValue("y_value");
  const zValue = getValue("z_value");
  if (!xValue && !yValue && !zValue) return false;

  return true;
}

export function isSuccessfulDigitizationRow(row: string[], headers: string[]) {
  const statusIndex = headers.indexOf("digitization_status");
  const digitizationStatus = statusIndex >= 0 ? String(row[statusIndex] || "").trim().toLowerCase() : "";
  return digitizationStatus === "digitized" || digitizationStatus === "partially_digitized";
}

export function isFailedDigitizationRow(row: string[], headers: string[]) {
  const statusIndex = headers.indexOf("digitization_status");
  const digitizationStatus = statusIndex >= 0 ? String(row[statusIndex] || "").trim().toLowerCase() : "";
  return digitizationStatus === "failed" || digitizationStatus === "too_low_resolution" || digitizationStatus === "axis_unreadable" || digitizationStatus === "legend_unreadable";
}

export function tableForPanel(
  table: AuditTable | undefined,
  panel: PanelRead,
  rowFilter: ((row: string[], headers: string[]) => boolean) | undefined = undefined,
): AuditTable | null {
  if (!table?.headers?.length || !table.rows?.length) return null;
  const panelIndex = table.headers.indexOf("panel_id");
  if (panelIndex < 0) return null;
  const normalizedPanelId = normalizePanelId(panel.panel_id);
  if (!normalizedPanelId) return null;

  const rows = table.rows.filter((row) => {
    const rowPanelId = normalizePanelId(String(row[panelIndex] || ""));
    if (rowPanelId !== normalizedPanelId) return false;
    if (!rowFilter) return true;
    return rowFilter(row, table.headers);
  });
  if (!rows.length) return null;

  const compact = compactAuditTable(table.headers, rows);
  return {
    headers: compact.headers,
    rows: compact.rows,
    total: rows.length,
  };
}

export function tableForAsset(
  table: AuditTable | undefined,
  asset: AssetRead,
  rowFilter: ((row: string[], headers: string[]) => boolean) | undefined = undefined,
): AuditTable | null {
  if (!table?.headers?.length || !table.rows?.length) return null;
  const sourceIndex = table.headers.indexOf("source_image");
  if (sourceIndex < 0) return null;
  const sourceNames = assetSourceImageNames(asset);
  if (!sourceNames.size) return null;

  const rows = table.rows.filter((row) => {
    const rowSource = String(row[sourceIndex] || "").trim();
    const rowSourceName = rowSource.split(/[\\/]/).pop() || "";
    if (!sourceNames.has(rowSource) && !sourceNames.has(rowSourceName)) return false;
    if (!rowFilter) return true;
    return rowFilter(row, table.headers);
  });
  if (!rows.length) return null;

  const headers = table.headers.filter((_, index) => index !== sourceIndex);
  const compact = compactAuditTable(headers, rows.map((row) => row.filter((_, index) => index !== sourceIndex)));
  return {
    headers: compact.headers,
    rows: compact.rows,
    total: rows.length,
  };
}

export function canonicalAssetLabel(asset: AssetRead, tables: Record<string, AuditTable>) {
  return panelIdFromAuditTables(asset, tables)
    || panelIdFromMetadata(asset)
    || normalizePanelId(asset.label || "")
    || `asset-${asset.id}`;
}

export function panelIdFromAuditTables(asset: AssetRead, tables: Record<string, AuditTable>) {
  const sourceNames = assetSourceImageNames(asset);
  if (!sourceNames.size) return "";

  const tableOrder = ["chart_facts", "chart_points", "heatmap_candidates", "metric_rows", "metric_candidates", "chart_digitization", "image_observations"];
  for (const tableKey of tableOrder) {
    const table = tables[tableKey];
    if (!table?.headers?.length || !table.rows?.length) continue;
    const sourceIndex = table.headers.indexOf("source_image");
    const panelIndex = table.headers.indexOf("panel_id");
    if (sourceIndex < 0 || panelIndex < 0) continue;

    for (const row of table.rows) {
      const sourceName = String(row[sourceIndex] || "").split(/[\\/]/).pop() || "";
      if (!sourceNames.has(sourceName)) continue;
      const panelId = normalizePanelId(String(row[panelIndex] || ""));
      if (panelId) return panelId;
    }
  }

  return "";
}

export function assetPanelMatches(asset: AssetRead, panel: PanelRead) {
  const panelId = normalizePanelId(panel.panel_id);
  if (panelId) {
    return panelIdFromMetadata(asset) === panelId || normalizePanelId(asset.label || "") === panelId;
  }
  const panelLetter = normalizePanelLetter(panel.panel_id);
  if (!panelLetter) return false;
  const metadata = asset.metadata || {};
  const assetLetter = normalizePanelLetter(
    metadataString(metadata.panel_id)
      || metadataString(metadata.panel_label)
      || metadataString(metadata.panel)
      || metadataString(metadata.mineru_alt_text)
      || "",
  );
  return assetLetter === panelLetter;
}

export function panelIdFromMetadata(asset: AssetRead) {
  const metadata = asset.metadata || {};
  const directPanel = normalizePanelId(metadataString(metadata.panel_id) || metadataString(metadata.source_panel_id));
  if (directPanel) return directPanel;

  const figureId = normalizeFigureId(
    metadataString(metadata.parent_figure_id)
      || metadataString(metadata.figure_id)
      || metadataString(metadata.figure_group_key)
      || asset.label
      || "",
  );
  if (!figureId) return "";

  const panelLetter = normalizePanelLetter(
    metadataString(metadata.panel_id)
      || metadataString(metadata.panel_label)
      || metadataString(metadata.panel)
      || "",
  ) || "a";
  return `${figureId}-${panelLetter}`;
}

export function panelTypeFromAuditPanel(panel: PanelRead, assets: AssetRead[], tables: Record<string, AuditTable>) {
  if (tableForPanel(tables.chart_facts || tables.chart_points, panel, isUsableChartFactRow)) return "numeric_chart";
  if (tableForPanel(tables.chart_digitization, panel, isSuccessfulDigitizationRow)) return "numeric_chart";
  if (tableForPanel(tables.chart_facts || tables.chart_points, panel, isFailedDigitizationRow)) return "chart_failed";
  if (tableForPanel(tables.chart_digitization, panel, isFailedDigitizationRow)) return "chart_failed";
  if (tableForPanel(tables.metric_rows, panel)) return "benchmark_metric";

  for (const asset of assets.filter((a) => assetPanelMatches(a, panel))) {
    if (tableForAsset(tables.chart_facts || tables.chart_points, asset, isUsableChartFactRow)) return "numeric_chart";
    if (tableForAsset(tables.chart_digitization, asset, isSuccessfulDigitizationRow)) return "numeric_chart";
    if (tableForAsset(tables.chart_facts || tables.chart_points, asset, isFailedDigitizationRow)) return "chart_failed";
    if (tableForAsset(tables.chart_digitization, asset, isFailedDigitizationRow)) return "chart_failed";
    if (tableForAsset(tables.metric_rows, asset)) return "benchmark_metric";
    if (assetLooksChartLike(asset)) return "chart_candidate";
  }
  return "";
}

export function normalizePanelId(value: string) {
  const trimmed = value.trim().toLowerCase();
  const match = trimmed.match(/\bfig(?:ure)?[-_\s.]*(\d+)[-_\s.]*([a-z])\b/i);
  if (match) return `fig-${match[1]}-${match[2].toLowerCase()}`;
  return "";
}

export function normalizeFigureId(value: string) {
  const trimmed = value.trim().toLowerCase();
  const match = trimmed.match(/\bfig(?:ure)?[-_\s.]*(\d+)\b/i);
  if (!match) return "";
  return `fig-${match[1]}`;
}

export function normalizePanelLetter(value: string) {
  const trimmed = value.trim().toLowerCase();
  if (/^[a-z]$/.test(trimmed)) return trimmed;
  const panelId = normalizePanelId(trimmed);
  if (panelId) return panelId.split("-").pop() || "";
  return "";
}

export function metadataString(value: unknown) {
  return typeof value === "string" ? value : "";
}

export function compactAuditTable(headers: string[], rows: string[][]) {
  const preferred = [
    "series_name", "point_index", "x_label", "x_axis_label", "x_value", "x_unit",
    "y_label", "y_axis_label", "y_value", "y_unit", "confidence", "digitization_status",
    "needs_review", "source_phase", "metric_name", "series", "condition", "value",
    "value_min", "value_max", "unit", "scale_factor", "evidence_type",
    "matched_target_group_id", "extraction_source",
  ];
  const indexes = preferred
    .map((name) => headers.indexOf(name))
    .filter((index) => index >= 0);
  if (!indexes.length) return { headers, rows };
  return {
    headers: indexes.map((index) => headers[index]),
    rows: rows.map((row) => indexes.map((index) => row[index] || "")),
  };
}

export function assetLooksChartLike(asset: AssetRead) {
  const metadata = asset.metadata || {};
  const values = [
    metadata.mineru_type,
    metadata.panel_type,
    metadata.recommended_extractor_hint,
    metadata.asset_scope,
  ].map((value) => String(value || "").toLowerCase());
  return values.some((value) => value.includes("chart") || value.includes("plot") || value.includes("numeric"));
}

export function assetSourceImageNames(asset: AssetRead) {
  const metadata = asset.metadata || {};
  const candidates = [
    metadata.mineru_img_path,
    metadata.image_path,
    metadata.img_path,
    metadata.source_image,
    metadata.filename,
  ];
  const names = new Set<string>();
  for (const candidate of candidates) {
    if (typeof candidate !== "string" || !candidate.trim()) continue;
    const name = candidate.split(/[\\/]/).pop();
    if (name) names.add(name);
  }
  return names;
}
