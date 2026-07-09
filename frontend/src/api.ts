export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");
export const API_DISPLAY_URL = API_BASE_URL || "same-origin proxy";

function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${normalizedPath}`;
}

export type AssetRead = {
  id: number;
  paper_id: number;
  figure_id: number | null;
  asset_type: string;
  label: string | null;
  page_number: number | null;
  image_url: string;
  mime_type: string;
  width: number | null;
  height: number | null;
  metadata: Record<string, unknown>;
  latest_extraction: ExtractionRead | null;
  created_at: string;
};

export type PanelRead = {
  id: number;
  figure_id: number;
  asset_id: number | null;
  panel_id: string;
  panel_type?: string;
  domain_task: string;
  extractor: string;
  extraction_priority: string;
  panel_index: number;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type FigureRead = {
  id: number;
  paper_id: number;
  figure_id: string;
  caption_text: string | null;
  page_number: number | null;
  is_multi_panel: boolean;
  panel_count: number;
  metadata: Record<string, unknown>;
  created_at: string;
  panels: PanelRead[];
  assets: number[];
};

export type PaperRead = {
  id: number;
  title: string;
  original_filename: string;
  status: string;
  page_count: number | null;
  asset_count: number;
  figure_count: number;
  text_preview: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  audit_summary: AuditSummary | null;
  assets: AssetRead[];
  figures: FigureRead[];
};

export type ExtractionRead = {
  id: number;
  asset_id: number;
  figure_id: number | null;
  status: string;
  query: string | null;
  csv_url: string | null;
  result: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

export type BatchExtractionResponse = {
  paper_id: number;
  total_assets: number;
  created_count: number;
  skipped_count: number;
  skipped_asset_ids: number[];
  extractions: ExtractionRead[];
};

export type ChartOnlyRunBatchItem = {
  paper_id: number;
  status: string;
  detail: string | null;
};

export type ChartOnlyRunBatchResponse = {
  total: number;
  queued: number;
  skipped: number;
  not_found: number;
  items: ChartOnlyRunBatchItem[];
};

export type AuditSummary = {
  audit_path: string;
  figure_count: number;
  panel_count: number;
  processed_panels: number;
  progress_percent: number;
  metric_rows: number;
  benchmark_metrics?: number;
  metric_candidates?: number;
  rejected_metric_rows: number;
  chart_facts?: number;
  chart_points: number;
  image_observations: number;
  digitization_results: number;
  errors: number;
  failure_events?: number;
  first_error?: string | null;
  result_state: string;
  source?: string;
  status?: string;
};

export type AuditTable = {
  headers: string[];
  rows: string[][];
  total: number;
};

export type LocalArtifactRead = {
  id: string;
  title: string;
  content_list_path: string;
  absolute_content_list_path: string;
  markdown_path: string | null;
  layout_path: string | null;
  source_path: string | null;
  image_count: number;
  kind: string;
  audit_summary: AuditSummary | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (error) {
    throw new Error(networkErrorMessage(path, error));
  }
  if (!response.ok) {
    let message = `Request failed with ${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) message = String(payload.detail);
    } catch {
      message = response.statusText || message;
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

async function requestNoContent(path: string, init?: RequestInit): Promise<void> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch (error) {
    throw new Error(networkErrorMessage(path, error));
  }
  if (!response.ok) {
    let message = `Request failed with ${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) message = String(payload.detail);
    } catch {
      message = response.statusText || message;
    }
    throw new Error(message);
  }
}

export function listPapers() {
  return request<PaperRead[]>("/papers");
}

export function listLocalArtifacts() {
  return request<LocalArtifactRead[]>("/papers/local-artifacts");
}

export function importLocalArtifact(contentListPath: string, title?: string) {
  return request<PaperRead>("/papers/local-artifacts/import", {
    method: "POST",
    body: JSON.stringify({ content_list_path: contentListPath, title: title?.trim() || null }),
  });
}

export function getPaper(id: number) {
  return request<PaperRead>(`/papers/${id}`);
}

export async function uploadPaper(file: File, title?: string) {
  const form = new FormData();
  form.append("file", file);
  if (title?.trim()) form.append("title", title.trim());
  return request<PaperRead>("/papers/upload", { method: "POST", body: form });
}

export function retryPaperParse(paperId: number) {
  return request<PaperRead>(`/papers/${paperId}/retry`, { method: "POST" });
}

export function deletePaper(paperId: number) {
  return (async () => {
    try {
      await requestNoContent(`/papers/${paperId}`, { method: "DELETE" });
      return;
    } catch (error) {
      if (error instanceof Error && (error.message.includes("405") || error.message.includes("Method Not Allowed"))) {
        await requestNoContent(`/papers/${paperId}/delete`, { method: "POST" });
        return;
      }
      throw error;
    }
  })();
}

export function listPaperFigures(paperId: number) {
  return request<FigureRead[]>(`/papers/${paperId}/figures`);
}

export function getPaperAuditTables(paperId: number) {
  return request<{ tables: Record<string, AuditTable>; source?: string | null; audit_path?: string | null }>(`/papers/${paperId}/audit-tables`, {
    cache: "no-store",
  });
}

export function runPaperChartOnly(paperId: number) {
  return request<PaperRead>(`/papers/${paperId}/chart-only/run`, { method: "POST" });
}

export function runPaperChartOnlyBatch(paperIds: number[]) {
  return request<ChartOnlyRunBatchResponse>("/papers/chart-only/run-batch", {
    method: "POST",
    body: JSON.stringify({ paper_ids: paperIds }),
  });
}

export function getFigure(figureId: number) {
  return request<FigureRead>(`/papers/figures/${figureId}`);
}

export function getExtraction(id: number) {
  return request<ExtractionRead>(`/extractions/${id}`);
}

export function assetUrl(asset: AssetRead) {
  return apiUrl(asset.image_url);
}

export function csvUrl(extraction: ExtractionRead) {
  return extraction.csv_url ? apiUrl(extraction.csv_url) : "";
}

function networkErrorMessage(path: string, error: unknown): string {
  const target = apiUrl(path);
  const detail = error instanceof Error ? error.message : String(error || "");
  return [
    `无法连接后端 API: ${target}`,
    "请确认后端已启动、Vite 代理配置正确，或 VITE_API_BASE_URL 指向了正确地址。",
    detail ? `浏览器错误: ${detail}` : "",
  ].filter(Boolean).join(" ");
}
