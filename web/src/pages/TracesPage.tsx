import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Search, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { StatusPill } from "../components/StatusPill";

const PAGE_SIZE = 50;

export function TracesPage() {
  const [urlParams, setUrlParams] = useSearchParams();
  const [draft, setDraft] = useState(urlParams.get("search") ?? urlParams.get("semantic") ?? "");
  const [semantic, setSemantic] = useState(Boolean(urlParams.get("semantic")));
  const page = Number(urlParams.get("page") ?? 1);
  const facets = useQuery({ queryKey: ["facets"], queryFn: api.facets });

  const queryParams = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  for (const key of ["search", "semantic", "outcome", "failure_mode", "app", "trace_ids"]) {
    const value = urlParams.get(key);
    if (value) queryParams.set(key, value);
  }
  const traces = useQuery({ queryKey: ["traces", queryParams.toString()], queryFn: () => api.traces(queryParams) });

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(urlParams);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("page" in changes)) next.set("page", "1");
    setUrlParams(next);
  };

  const runSearch = () => update(semantic ? { semantic: draft, search: "" } : { search: draft, semantic: "" });
  const total = traces.data?.total ?? 0;
  const ranked = traces.data?.ranked_by === "semantic";

  return (
    <div className="wide-page traces-page page-enter">
      <div className="page-title-row">
        <div>
          <div className="eyebrow">DATASET</div>
          <h1>Traces</h1>
          <p>{total.toLocaleString()} redacted conversations{ranked ? " ranked by meaning" : ""}</p>
        </div>
        <div className="trace-controls">
          <label className="search-field">
            {semantic ? <Sparkles size={15} /> : <Search size={15} />}
            <span className="sr-only">Search traces</span>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && runSearch()}
              placeholder={semantic ? "Describe what you're looking for" : "Search text and ids"}
            />
          </label>
          <button
            type="button"
            className={semantic ? "toggle-button on" : "toggle-button"}
            aria-pressed={semantic}
            onClick={() => setSemantic((value) => !value)}
            title="Semantic search ranks by meaning rather than exact substring"
          >
            <Sparkles size={14} /> Meaning
          </button>
          <select aria-label="Filter by app" value={urlParams.get("app") ?? ""} onChange={(event) => update({ app: event.target.value })}>
            <option value="">All apps</option>
            {(facets.data?.app ?? []).map((row: any) => (
              <option key={row.value} value={row.value}>{row.value} ({row.count})</option>
            ))}
          </select>
          <select aria-label="Filter by outcome" value={urlParams.get("outcome") ?? ""} onChange={(event) => update({ outcome: event.target.value })}>
            <option value="">All outcomes</option>
            {(facets.data?.outcome ?? []).map((row: any) => (
              <option key={row.value} value={row.value}>{row.value} ({row.count})</option>
            ))}
          </select>
          <select
            aria-label="Filter by failure mode"
            value={urlParams.get("failure_mode") ?? ""}
            onChange={(event) => update({ failure_mode: event.target.value })}
          >
            <option value="">All failure modes</option>
            {(facets.data?.failure_mode ?? []).map((row: any) => (
              <option key={row.value} value={row.value}>{row.value.replaceAll("_", " ")} ({row.count})</option>
            ))}
          </select>
        </div>
      </div>

      {urlParams.get("trace_ids") && (
        <div className="evidence-filter">
          Showing the exact evidence set behind an answer ·{" "}
          <button type="button" onClick={() => update({ trace_ids: "" })}>Clear</button>
        </div>
      )}

      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Trace</th><th>App</th><th>Outcome</th><th>Failure mode</th>
              <th>Turns</th><th>Duration</th><th>Cost</th>
              <th>{ranked ? "Relevance" : "Started"}</th><th aria-label="Open" />
            </tr>
          </thead>
          <tbody>
            {(traces.data?.items ?? []).map((trace: any) => (
              <tr key={trace.id}>
                <td>
                  <Link className="trace-summary" to={`/traces/${trace.id}`}>
                    <span className="mono">{trace.id}</span>
                    <strong>{trace.summary}</strong>
                    <q>{trace.user_request}</q>
                  </Link>
                </td>
                <td>{trace.app}</td>
                <td><StatusPill value={trace.outcome} /></td>
                <td>{trace.failure_mode.replaceAll("_", " ")}</td>
                <td className="mono">{trace.turns}</td>
                <td className="mono">{trace.duration_ms ? `${(trace.duration_ms / 1000).toFixed(1)}s` : "Unknown"}</td>
                <td className="mono">${trace.cost_usd.toFixed(4)}</td>
                <td className="mono">
                  {ranked ? (trace.relevance ?? 0).toFixed(3) : new Date(trace.started_at).toLocaleDateString()}
                </td>
                <td><ChevronRight size={15} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!traces.isLoading && !traces.data?.items.length && (
          <div className="empty-state">No traces match these filters.</div>
        )}
      </div>

      <div className="pagination">
        <span>Page {page} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
        <div>
          <button type="button" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}>
            <ChevronLeft size={16} /> Previous
          </button>
          <button type="button" disabled={page * PAGE_SIZE >= total} onClick={() => update({ page: String(page + 1) })}>
            Next <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
