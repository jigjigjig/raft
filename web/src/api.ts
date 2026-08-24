import type { AnalysisRun, Answer, QuestionPlan, Trace } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  home: () => request<any>("/api/home"),
  plan: (question: string, parentRunId?: string | null) =>
    request<QuestionPlan>("/api/questions/plan", {
      method: "POST",
      body: JSON.stringify({ question, parent_run_id: parentRunId ?? null }),
    }),
  createRun: (question: string, plan: QuestionPlan, parentRunId?: string | null) =>
    request<AnalysisRun>("/api/analysis-runs", {
      method: "POST",
      body: JSON.stringify({ question, plan, parent_run_id: parentRunId ?? null }),
    }),
  run: (id: string) => request<AnalysisRun>(`/api/analysis-runs/${id}`),
  confirm: (id: string, aspect_question?: string) =>
    request<AnalysisRun>(`/api/analysis-runs/${id}/confirm`, {
      method: "POST",
      body: JSON.stringify({ aspect_question }),
    }),
  resume: (id: string) =>
    request<AnalysisRun>(`/api/analysis-runs/${id}/resume`, { method: "POST", body: "{}" }),
  answer: (id: string) => request<Answer>(`/api/analysis-runs/${id}/answer`),
  aspects: () => request<any[]>("/api/aspects"),
  verification: (aspectId: string) => request<any>(`/api/aspects/${aspectId}/verification`),
  traces: (params: URLSearchParams) => request<any>(`/api/traces?${params.toString()}`),
  facets: () => request<any>("/api/traces/facets"),
  trace: (id: string) => request<Trace>(`/api/traces/${id}`),
  explain: (spanId: string) => request<any>(`/api/spans/${spanId}/explain`, { method: "POST", body: "{}" }),
  webSearch: (spanId: string) => request<any>(`/api/spans/${spanId}/web-search`, { method: "POST", body: "{}" }),
  settings: () => request<any>("/api/settings/status"),
  preflight: () => request<any>("/api/settings/preflight"),
  resetDemo: (traceCount: number, seed: number) =>
    request<any>("/api/settings/reset-demo", {
      method: "POST",
      body: JSON.stringify({ trace_count: traceCount, seed }),
    }),
};
