import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, CircleAlert, Clock3, Coins, Repeat, Search, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { StatusPill } from "../components/StatusPill";
import { StreamingText } from "../components/aicss/TextOutput";
import type { Span, Trace } from "../types";

export function TraceDetailPage() {
  const { traceId = "" } = useParams();
  const trace = useQuery({ queryKey: ["trace", traceId], queryFn: () => api.trace(traceId) });
  if (!trace.data) return <div className="wide-page"><div className="skeleton large" /></div>;
  const item = trace.data;
  const collapsed = collapseRepeats(item.spans);

  return (
    <div className="wide-page trace-detail-page page-enter">
      <Link className="back-link" to="/traces"><ArrowLeft size={15} /> All traces</Link>
      <div className="trace-header">
        <div>
          <div className="eyebrow">TRACE · <span className="mono">{item.id}</span></div>
          <h1>{item.summary}</h1>
          <p>{new Date(item.started_at).toLocaleString()} · {item.app} · <span className="mono">{item.model}</span></p>
        </div>
        <StatusPill value={item.outcome} />
      </div>

      <div className="trace-layout">
        <div className="trace-main">
          <section className="autopsy-panel">
            <div className="panel-title"><Sparkles size={15} /> Autopsy</div>
            <StreamingText text={item.autopsy} />
          </section>

          <section>
            <div className="section-heading">
              <h2>Timeline</h2>
              <span>Width is cost. Identical repeats are collapsed.</span>
            </div>
            <CostTrack spans={collapsed} total={item.cost_usd} />
            <div className="span-list">
              {collapsed.map((span) => <SpanRow key={span.id} span={span} traceCost={item.cost_usd} />)}
            </div>
          </section>
        </div>

        <aside className="trace-sidebar">
          <div className="metadata-panel">
            <h2>Shape</h2>
            <dl>
              <div><dt>Intent</dt><dd>{item.intent_label || "Not recorded"}</dd></div>
              <div><dt>Failure mode</dt><dd>{item.failure_mode.replaceAll("_", " ")}</dd></div>
              <div><dt>Got what they wanted</dt><dd>{item.intent_satisfied}</dd></div>
              <div><dt>Gave up</dt><dd>{item.user_gave_up ? "yes" : "no"}</dd></div>
              <div><dt>Ended by</dt><dd>{item.ended_by}</dd></div>
              <div><dt>Ending sentiment</dt><dd>{item.sentiment_end}</dd></div>
              <div><dt>Turns</dt><dd>{item.turns}</dd></div>
              <div><dt>Rephrases</dt><dd>{item.rephrase_count}</dd></div>
              <div><dt>Tool calls</dt><dd>{item.tool_calls}{item.repeated_identical_calls ? ` (${item.repeated_identical_calls} identical)` : ""}</dd></div>
              <div><dt>Tools</dt><dd>{item.distinct_tools.length ? item.distinct_tools.join(", ") : "None"}</dd></div>
            </dl>
          </div>
          <div className="metadata-panel">
            <h2>Capture</h2>
            <dl>
              <div><dt>Duration</dt><dd>{item.duration_ms ? `${(item.duration_ms / 1000).toFixed(1)} s of model latency` : "Unknown"}</dd></div>
              <div><dt>Tokens</dt><dd>{(item.tokens_input + item.tokens_output).toLocaleString()}</dd></div>
              <div><dt>Cost</dt><dd>${item.cost_usd.toFixed(4)}</dd></div>
              <div><dt>Completeness</dt><dd>{item.capture_completeness}</dd></div>
              <div><dt>Redaction</dt><dd>{item.redaction_status}</dd></div>
              <div><dt>Guardrail</dt><dd>{item.guardrail_status.replaceAll("_", " ")}</dd></div>
            </dl>
          </div>
          <div className="metadata-panel">
            <h2>What they asked for</h2>
            <blockquote>“{item.user_request}”</blockquote>
          </div>
          <div className="metadata-panel">
            <h2>What happened</h2>
            <p>{item.what_happened}</p>
          </div>
        </aside>
      </div>
    </div>
  );
}

type CollapsedSpan = Span & { collapsedCount: number };

/** Three identical failing calls render as one block marked x3. */
function collapseRepeats(spans: Span[]): CollapsedSpan[] {
  const out: CollapsedSpan[] = [];
  for (const span of spans) {
    const previous = out[out.length - 1];
    if (
      previous &&
      previous.type === span.type &&
      previous.name === span.name &&
      previous.content_redacted === span.content_redacted
    ) {
      previous.collapsedCount += 1;
      previous.cost_usd += span.cost_usd;
      previous.tokens_in += span.tokens_in;
      previous.tokens_out += span.tokens_out;
      continue;
    }
    out.push({ ...span, collapsedCount: 1 });
  }
  return out;
}

function CostTrack({ spans, total }: { spans: CollapsedSpan[]; total: number }) {
  const safeTotal = total || 1;
  return (
    <div className="cost-track" role="img" aria-label="Span cost distribution">
      {spans.map((span) => (
        <span
          key={span.id}
          className={`cost-segment ${span.type} ${span.status === "error" ? "error" : ""}`}
          style={{ flexGrow: Math.max(0.6, (span.cost_usd / safeTotal) * 100) }}
          title={`${span.name || span.type} · $${span.cost_usd.toFixed(5)}`}
        />
      ))}
    </div>
  );
}

// A timeline row is a label, not an identifier: `search_catalog` reads as
// "Search catalog" here while the metadata panel and group cards keep the
// monospace identifier. Sentence case, not title case - capitalize would
// have made it "Search Catalog".
function spanLabel(span: { name?: string | null; type: string }) {
  const words = (span.name || span.type).replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function SpanRow({ span, traceCost }: { span: CollapsedSpan; traceCost: number }) {
  const [open, setOpen] = useState(false);
  const explain = useMutation({ mutationFn: () => api.explain(span.id) });
  const search = useMutation({ mutationFn: () => api.webSearch(span.id) });
  const share = traceCost ? (span.cost_usd / traceCost) * 100 : 0;

  return (
    <article className={`span-row ${span.status === "error" ? "has-error" : ""}`}>
      <button className="span-summary" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="span-index mono">{String(span.index + 1).padStart(2, "0")}</span>
        <span className="span-type">
          {span.status === "error" ? <CircleAlert size={15} /> : <span className="span-dot" />}
          {spanLabel(span)}
          {span.collapsedCount > 1 && <em className="repeat-badge"><Repeat size={11} /> ×{span.collapsedCount}</em>}
        </span>
        <span className="span-stat"><Clock3 size={13} /> {span.duration_ms ? `${span.duration_ms} ms` : "Unknown"}</span>
        <span className="span-stat"><Coins size={13} /> ${span.cost_usd.toFixed(5)} · {share.toFixed(0)}%</span>
        <ChevronDown className={open ? "chevron open" : "chevron"} size={15} />
      </button>
      {open && (
        <div className="span-body">
          <pre>{span.content_redacted}</pre>
          {span.collapsedCount > 1 && (
            <p className="span-note">
              This exact call was made {span.collapsedCount} times in a row. Cost and tokens above are the total for all
              {" "}{span.collapsedCount}.
            </p>
          )}
          <div className="span-actions">
            <button type="button" onClick={() => explain.mutate()} disabled={explain.isPending}>
              <Sparkles size={14} /> {explain.isPending ? "Explaining…" : "Explain this step"}
            </button>
            {span.error_code && (
              <button type="button" onClick={() => search.mutate()} disabled={search.isPending}>
                <Search size={14} /> {search.isPending ? "Looking up…" : "Look this up"}
              </button>
            )}
          </div>
          {explain.data && (
            <div className="tool-result">
              <strong>Step explanation</strong>
              <p>{explain.data.explanation}</p>
              <small>{explain.data.mode}</small>
            </div>
          )}
          {search.data && (
            <div className="tool-result">
              <strong>Error lookup · {span.error_code}</strong>
              <p>{search.data.answer}</p>
              <small>{search.data.mode}</small>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
