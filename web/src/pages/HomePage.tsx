import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, ShieldCheck, TrendingDown } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { PromptInput } from "../components/aicss/PromptInput";
import { Bars } from "../components/Bars";

export function HomePage() {
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const home = useQuery({ queryKey: ["home"], queryFn: api.home });
  const ask = useMutation({
    mutationFn: async (value: string) => {
      const plan = await api.plan(value);
      return api.createRun(value, plan);
    },
    onSuccess: (run) => navigate(`/answers/${run.id}`),
  });

  const submit = (value = question) => {
    const trimmed = value.trim();
    if (trimmed) ask.mutate(trimmed);
  };

  const data = home.data;
  // `window` shadowed the global here. The count and the date range are only
  // rendered once the payload exists: defaulting to 0 printed "0 captured
  // conversations" on every cold load, which is a wrong number, not a blank one.
  const captureWindow = data?.window;
  const captureRange =
    captureWindow?.first && captureWindow?.last
      ? ` (${new Date(captureWindow.first).toLocaleDateString()} – ${new Date(captureWindow.last).toLocaleDateString()})`
      : "";

  return (
    <div className="home-page page-enter">
      <section className="home-hero">
        <div className="eyebrow"><ShieldCheck size={14} /> Redacted, traceable evidence</div>
        <h1>Ask your traces anything.</h1>
        <p>
          {/* The paragraph always renders. Only the count and the range wait for
              the payload: gating the whole sentence jumped the page three lines
              on every cold load, and defaulting the count to 0 printed a wrong
              number. This costs at most one line of shift and never lies. */}
          Raft turns {data ? `${data.trace_count.toLocaleString()} ` : ""}captured LLM conversations{captureRange} into
          a dataset you can question in plain English. Type anything — Raft decides how to answer, computes
          every number with code you can read, and links each one back to the conversations it came from.
        </p>
        <PromptInput value={question} onChange={setQuestion} onSubmit={() => submit()} pending={ask.isPending} />
        {ask.error && <div className="inline-error" role="alert">{(ask.error as Error).message}</div>}
        <div className="chip-row">
          {(data?.example_questions ?? []).map((example: string) => (
            <button key={example} type="button" className="chip" onClick={() => { setQuestion(example); submit(example); }}>
              {example}
            </button>
          ))}
        </div>
      </section>

      <section className="insights" aria-labelledby="insights-title">
        <div className="section-heading">
          <h2 id="insights-title">What the pile already says</h2>
          <span>Recomputed from the current data on every visit</span>
        </div>
        <div className="stat-row">
          {(data?.headline_stats ?? []).map((stat: any) => (
            <button key={stat.label} type="button" className="stat-card" onClick={() => submit(stat.question)}>
              <strong>{stat.value.toLocaleString()}</strong>
              <span>{stat.label}</span>
              <small>{(stat.share * 100).toFixed(1)}% of all conversations</small>
              <ArrowRight className="card-arrow" size={16} />
            </button>
          ))}
        </div>

        <div className="insight-columns">
          <article className="insight-panel">
            <header>
              <h3><TrendingDown size={15} /> Most common failure modes</h3>
              <button type="button" onClick={() => submit("Which failure mode costs me the most?")}>Ask about spend</button>
            </header>
            <Bars
              rows={(data?.top_failures ?? []).map((row: any) => ({
                key: row.key,
                label: row.key.replaceAll("_", " "),
                value: row.count,
                caption: `$${Number(row.cost ?? 0).toFixed(3)}`,
              }))}
              onSelect={(key) => navigate(`/traces?failure_mode=${encodeURIComponent(key)}`)}
            />
          </article>

          <article className="insight-panel">
            <header>
              <h3><ShieldCheck size={15} /> Most-asked things the agents cannot do</h3>
              <button type="button" onClick={() => submit("What are people asking for that my agent cannot do?")}>Cluster these</button>
            </header>
            <ul className="plain-list">
              {(data?.top_unmet_requests ?? []).map((row: any) => (
                <li key={`${row.app}-${row.key}`}>
                  <span>{row.key}</span>
                  <small className="mono">{row.app}</small>
                  <strong>{row.count}</strong>
                </li>
              ))}
            </ul>
          </article>
        </div>
      </section>
    </div>
  );
}
