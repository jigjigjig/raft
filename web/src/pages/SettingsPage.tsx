import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Copy, Database, ExternalLink, RefreshCw, Server, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../api";

export function SettingsPage() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const aspects = useQuery({ queryKey: ["aspects"], queryFn: api.aspects });
  const [traceCount, setTraceCount] = useState(847);
  const [seed, setSeed] = useState(20260821);
  const reset = useMutation({
    mutationFn: () => api.resetDemo(traceCount, seed),
    onSuccess: () => client.invalidateQueries(),
  });
  const data = settings.data;
  const analysis = data?.analysis;

  return (
    <div className="wide-page settings-page page-enter">
      <div className="page-title-row">
        <div>
          <div className="eyebrow">CONFIGURATION AND PROVENANCE</div>
          <h1>Settings</h1>
          <p>What computes each answer, and what has actually been observed</p>
        </div>
        {data && (
          <span className={`mode-badge ${data.mode}`}>
            {data.mode === "live" ? `Live · ${analysis?.provider}` : "Local mode · no model calls"}
          </span>
        )}
      </div>

      <section className="settings-section">
        <div className="section-heading">
          <h2>How answers are produced right now</h2>
          <span>Numbers are executed code in every mode</span>
        </div>
        <div className="pipeline-grid">
          {[
            ["Question routing", analysis?.planner],
            ["Aggregation", analysis?.aggregation],
            ["Aspect judgment", analysis?.aspect_judge],
            ["Cluster naming", analysis?.cluster_naming],
            ["Embeddings", analysis?.embeddings],
            ["Aspects defined", String(analysis?.aspects_defined ?? 0)],
          ].map(([label, value]) => (
            <div key={String(label)} className="pipeline-card">
              <span>{label}</span>
              <strong>{value || "—"}</strong>
            </div>
          ))}
        </div>
        <p className="section-note">{analysis?.note}</p>
        {analysis && !analysis.otari_features_available && (
          <p className="section-note warn">
            Otari-specific features (Routing policies, Guardrails, MCP planner tools, the hosted sandbox, web search)
            are not being exercised in this mode, so no evidence is claimed for them below.
          </p>
        )}
      </section>

      <section className="settings-section">
        <div className="section-heading">
          <h2>Model roles</h2>
          <span>Model choice lives in configuration, not application code</span>
        </div>
        <div className="table-shell">
          <table>
            <thead>
              <tr><th>Role and job</th><th>Primary</th><th>Fallback</th><th>Observed final</th><th>Request ID</th><th>Status</th></tr>
            </thead>
            <tbody>
              {(data?.roles ?? []).map((role: any) => (
                <tr key={role.name}>
                  <td><strong>{role.label}</strong><small>{role.job}</small></td>
                  <td className="model-cell">{role.primary}</td>
                  <td className="model-cell">{role.fallbacks.join(", ")}</td>
                  <td className="model-cell">{role.observed_final_model || "Not observed"}</td>
                  <td><RequestId value={role.last_request_id} /></td>
                  <td>
                    {role.last_status === "complete"
                      ? <span className="ready"><CheckCircle2 size={14} /> Complete</span>
                      : <span className="pending"><CircleDashed size={14} /> Not called yet</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="settings-grid">
        <section className="settings-section">
          <div className="section-heading">
            <h2>Otari features</h2>
            <span>A feature counts only once a live request has been observed</span>
          </div>
          <div className="feature-list">
            {(data?.features ?? []).map((feature: any) => {
              const observed = feature.status.includes("log_pending");
              return (
                <div key={feature.feature}>
                  <span className="feature-icon"><ShieldCheck size={15} /></span>
                  <div><strong>{feature.feature}</strong><p>{feature.notes}</p></div>
                  <span className={observed ? "ready" : "pending"}>
                    {observed ? <CheckCircle2 size={14} /> : <CircleDashed size={14} />}
                    {observed ? "Observed · log pending" : "Not exercised"}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="section-heading" style={{ marginTop: 28 }}>
            <h2>Saved aspects</h2>
            <span>Ask once, pay once — reused on every later question</span>
          </div>
          <div className="table-shell">
            <table>
              <thead><tr><th>Question</th><th>Type</th><th>Version</th><th>Evaluated</th><th>Yes</th></tr></thead>
              <tbody>
                {(aspects.data ?? []).map((aspect: any) => (
                  <tr key={aspect.id}>
                    <td><strong>{aspect.question}</strong><small>from “{aspect.created_from}”</small></td>
                    <td>{aspect.type}</td>
                    <td className="mono">v{aspect.version}</td>
                    <td className="mono">{aspect.evaluated}</td>
                    <td className="mono">{aspect.yes_count ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!aspects.data?.length && <div className="empty-state small">No aspect has been created yet.</div>}
          </div>
        </section>

        <aside className="settings-side">
          <div className="metadata-panel">
            <div className="panel-title"><Server size={15} /> Read-only MCP server</div>
            <dl>
              <div><dt>Status</dt><dd>{data?.mcp.status.replaceAll("_", " ")}</dd></div>
              <div><dt>Local URL</dt><dd className="mono">{data?.mcp.local_url}</dd></div>
              <div><dt>Public URL</dt><dd>{data?.mcp.public_url || "Not configured"}</dd></div>
              <div><dt>Tools</dt><dd>{(data?.mcp.tools ?? []).join(", ")}</dd></div>
            </dl>
          </div>

          <div className="metadata-panel">
            <h2>Budget</h2>
            <strong className="budget-number">${Number(data?.budget.local_run_allowance_usd ?? 0).toFixed(2)}</strong>
            <p>Local allowance per run. {data?.budget.note}</p>
          </div>

          <div className="metadata-panel">
            <div className="panel-title"><Database size={15} /> Dataset</div>
            <dl>
              <div><dt>Traces</dt><dd className="mono">{data?.dataset.trace_count?.toLocaleString()}</dd></div>
              <div><dt>Status</dt><dd>{data?.dataset.truth_status.replaceAll("_", " ")}</dd></div>
              <div><dt>Distinct asks</dt><dd className="mono">{data?.dataset.provenance?.distinct_user_requests ?? "—"}</dd></div>
              <div><dt>Embedding</dt><dd className="mono">{data?.dataset.provenance?.embedding_model ?? "—"}</dd></div>
              <div><dt>Vocabulary</dt><dd className="mono">{data?.dataset.provenance?.vocabulary_terms ?? "—"}</dd></div>
              <div><dt>Generator</dt><dd>{data?.dataset.provenance?.generator ?? "—"}</dd></div>
            </dl>
            <div className="reset-form">
              <label>
                Traces
                <input type="number" min={50} max={5000} value={traceCount} onChange={(event) => setTraceCount(Number(event.target.value))} />
              </label>
              <label>
                Seed
                <input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
              </label>
              <button type="button" className="primary-button" onClick={() => reset.mutate()} disabled={reset.isPending}>
                <RefreshCw size={14} className={reset.isPending ? "spin" : ""} />
                {reset.isPending ? "Rebuilding…" : "Regenerate dataset"}
              </button>
              <small>Rebuilds traces, spans and embeddings. Every saved answer and aspect is deleted with them.</small>
              {reset.data && <small className="ready">Built {reset.data.traces} traces, {reset.data.distinct_user_requests} distinct asks.</small>}
            </div>
          </div>

          <div className="metadata-panel">
            <h2>External tester URL</h2>
            {data?.public_app_url ? (
              <a href={data.public_app_url} target="_blank" rel="noreferrer">{data.public_app_url}<ExternalLink size={13} /></a>
            ) : (
              <p>Not configured. Set <span className="mono">RAFT_PUBLIC_APP_URL</span> after creating the Cloudflare Tunnel.</p>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function RequestId({ value }: { value?: string }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="muted">Not observed</span>;
  return (
    <button
      className="copy-id"
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
      }}
    >
      <span className="mono">{value}</span>
      {copied ? <CheckCircle2 size={13} /> : <Copy size={13} />}
    </button>
  );
}
