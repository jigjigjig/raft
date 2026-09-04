import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, CheckCircle2, ChevronRight, CircleDollarSign,
  Compass, Flame, PauseCircle, Sigma, TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Bars } from "../components/Bars";
import { GroupCard } from "../components/GroupCard";
import { CodeBlock } from "../components/aicss/CodeBlock";
import { TaskList } from "../components/aicss/TaskList";
import { TextResponse, StreamingText } from "../components/aicss/TextOutput";
import { ThinkingReasoning } from "../components/aicss/ThinkingReasoning";
import type { AnalysisRun } from "../types";

const terminal = new Set(["complete", "failed", "paused_budget", "paused_provider", "guardrail_blocked"]);

// A minutes-long wait read as "439–1129 seconds", which is the last thing
// someone reads before committing to it. Same range, in units a person counts in.
function duration(run: { estimate_seconds_min: number; estimate_seconds_max: number }) {
  const { estimate_seconds_min: low, estimate_seconds_max: high } = run;
  if (high < 120) return `${low}–${high} seconds`;
  return `${Math.max(1, Math.round(low / 60))}–${Math.round(high / 60)} minutes`;
}
// Both pause states stop the poll, keep their rows, and resume through the same button.
const paused = new Set(["paused_budget", "paused_provider"]);

const PATH_LABEL: Record<string, string> = {
  layer1: "Recorded trace shape",
  layer2_cluster: "Emergent clustering",
  layer3_aspect: "New per-trace aspect",
};

const PATH_BLURB: Record<string, string> = {
  layer1: "Every part of the question maps to a column Raft already records, so it becomes one SQL selection and one aggregation.",
  layer2_cluster: "The answer lives in what people wrote, which no column can hold. Raft embeds the eligible conversations and lets the groups emerge.",
  layer3_aspect: "Nothing recorded answers this, so Raft defines one reusable per-trace question and evaluates it over every eligible conversation.",
};

function taskState(run: AnalysisRun, stage: string): "pending" | "active" | "complete" {
  const order = ["planning", "evaluating", "aggregating", "synthesizing", "complete"];
  const status = run.status === "queued" ? "planning" : run.status;
  const current = Math.max(0, order.indexOf(status));
  const target = order.indexOf(stage);
  if (run.status === "complete" || current > target) return "complete";
  if (current === target) return "active";
  return "pending";
}

export function AnswerPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [aspectQuestion, setAspectQuestion] = useState("");
  const [showWork, setShowWork] = useState(false);
  const [followUp, setFollowUp] = useState("");

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: (query) => (terminal.has(query.state.data?.status ?? "") ? false : 300),
  });
  const run = runQuery.data;

  useEffect(() => {
    if (run?.aspect?.question) setAspectQuestion(run.aspect.question);
  }, [run?.aspect?.question]);

  const answerQuery = useQuery({
    queryKey: ["answer", runId],
    queryFn: () => api.answer(runId),
    enabled: run?.status === "complete",
  });
  const answer = answerQuery.data;

  const confirm = useMutation({
    mutationFn: () =>
      paused.has(run?.status ?? "") ? api.resume(runId) : api.confirm(runId, aspectQuestion),
    onSuccess: (next) => client.setQueryData(["run", runId], next),
  });

  const ask = useMutation({
    mutationFn: async (value: string) => {
      const plan = await api.plan(value, runId);
      return api.createRun(value, plan, runId);
    },
    onSuccess: (next) => navigate(`/answers/${next.id}`),
  });

  const tasks = useMemo(
    () =>
      run
        ? [
            { label: "Read the question and choose a path", state: taskState(run, "planning") },
            { label: `Evaluate ${run.total.toLocaleString()} eligible conversations`, state: taskState(run, "evaluating") },
            { label: "Execute the aggregation code", state: taskState(run, "aggregating") },
            { label: "Link quotes and evidence sets", state: taskState(run, "synthesizing") },
          ]
        : [],
    [run],
  );

  if (!run) return <div className="answer-page narrow-page"><div className="skeleton large" /></div>;

  const realGroups = (answer?.groups ?? []).filter((group) => group.key !== "__other__");

  return (
    <div className="answer-page narrow-page page-enter">
      <Link className="back-link" to="/"><ArrowLeft size={15} /> New question</Link>
      <div className="question-label">QUESTION</div>
      <h1 className="answer-question">{run.question}</h1>
      {run.parent_run_id && (
        <Link className="parent-link" to={`/answers/${run.parent_run_id}`}>Follow-up · see the question this narrowed</Link>
      )}

      <section className={`plan-card ${run.path}`}>
        <div className="plan-head">
          <span className={`path-badge ${run.path}`}>{PATH_LABEL[run.path] ?? run.path}</span>
        </div>
        <p className="plan-blurb">{PATH_BLURB[run.path]}</p>
        {run.filters?.length > 0 && (
          <div className="plan-filters">
            <span className="plan-filter-label">Reading only</span>
            {run.filters.map((filter) => <span key={filter} className="chip static">{filter}</span>)}
          </div>
        )}
        {run.rationale?.length > 0 && (
          <ul className="plan-rationale">
            {run.rationale.map((line) => <li key={line}>{line}</li>)}
          </ul>
        )}
      </section>

      {run.status === "awaiting_confirmation" && (
        <section className="confirmation-card" aria-labelledby="confirm-title">
          <div className="confirmation-icon"><CircleDollarSign size={20} /></div>
          <div className="confirmation-content">
            <div className="eyebrow">NEW ASPECT REQUIRED</div>
            <h2 id="confirm-title">
              {run.estimate_usd > 0
                ? `About $${run.estimate_usd.toFixed(2)} and ${duration(run)}. Run it?`
                : `About ${duration(run)}, no model spend. Run it?`}
            </h2>
            <p>
              No saved field answers this. Raft will evaluate one reusable question against every eligible conversation
              and cache the result, so asking again is free.
            </p>
            <label htmlFor="aspect-question">Per-trace question — edit it if Raft read you wrong</label>
            <textarea id="aspect-question" rows={2} value={aspectQuestion} onChange={(event) => setAspectQuestion(event.target.value)} />
            <div className="confirm-meta">
              <span>{run.total.toLocaleString()} eligible conversations</span>
              <span>Boolean result</span>
              <span>{run.estimate_usd > 0 ? "Routed model" : "Local semantic judge"}</span>
            </div>
            <button
              className="primary-button"
              type="button"
              onClick={() => confirm.mutate()}
              disabled={confirm.isPending || aspectQuestion.trim().length < 3}
            >
              {confirm.isPending ? "Starting…" : "Evaluate every conversation"}
              <ChevronRight size={16} />
            </button>
          </div>
        </section>
      )}

      {!terminal.has(run.status) && run.status !== "awaiting_confirmation" && (
        <div className="analysis-progress">
          <ThinkingReasoning title={run.message}>
            <p>
              Raft chose <span className="mono">{run.path}</span>. Results are written after each batch, so the run can
              pause and resume without losing work.
            </p>
          </ThinkingReasoning>
          <TaskList tasks={tasks} completed={run.completed} total={run.total} />
          <div className="progress-track">
            <span style={{ width: `${run.total ? Math.min(100, (run.completed / run.total) * 100) : 3}%` }} />
          </div>
        </div>
      )}

      {paused.has(run.status) && (
        <section className="pause-card">
          <PauseCircle size={22} />
          <div>
            <h2>
              {run.status === "paused_provider"
                ? "Run paused when Otari stopped answering"
                : "Run paused before exceeding the budget"}
            </h2>
            <p>{run.message} Completed rows are saved; no partial total is presented as an answer.</p>
          </div>
          <button className="primary-button" type="button" onClick={() => confirm.mutate()}>Resume missing rows</button>
        </section>
      )}

      {run.status === "failed" && (
        <div className="inline-error" role="alert"><strong>Analysis failed.</strong> {run.error || run.message}</div>
      )}

      {answer && (
        <>
          {answer.is_overview && (
            <section className="overview-banner">
              <Compass size={18} />
              <div>
                <strong>This is an overview, not an answer to what you typed.</strong>
                <p>
                  Nothing in your question matches the words these conversations use, so Raft could not narrow to a
                  subject. Below is the shape of the whole dataset. Pick one of these to ask something it can pin
                  down — each is built from wording the conversations actually contain.
                </p>
                <div className="chip-row left">
                  {answer.suggestions.map((suggestion) => (
                    <button key={suggestion} type="button" className="chip" onClick={() => ask.mutate(suggestion)}>
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            </section>
          )}

          <section className="answer-summary">
            <div className="answer-kicker">
              <CheckCircle2 size={15} /> {answer.denominator.toLocaleString()} conversations counted
              {answer.excluded_count > 0 && ` · ${answer.excluded_count.toLocaleString()} outside this question`}
              <button
                className="inline-work-toggle"
                type="button"
                onClick={() => {
                  const opening = !showWork;
                  setShowWork(opening);
                  // Opening a section below the fold with no movement is no
                  // feedback at all.
                  // No `behavior` key: it inherits `scroll-behavior` from CSS,
                  // which the reduced-motion block forces back to auto.
                  if (opening) document.querySelector(".work-section")?.scrollIntoView({ block: "start" });
                }}
                aria-expanded={showWork}
              >
                Show the work
              </button>
            </div>
            <h2 className="answer-headline">{answer.headline}</h2>
            <TextResponse><StreamingText text={answer.interpretation} /></TextResponse>
          </section>

          {answer.standouts.length > 0 && (
            <section className="standout-section">
              <div className="section-heading">
                <h2><Flame size={15} /> What stands out</h2>
                <span>Groups that behave unlike the rest, not the biggest ones</span>
              </div>
              <div className="standout-list">
                {answer.standouts.map((standout) => (
                  <Link
                    key={`${standout.group_key}-${standout.kind}`}
                    className="standout"
                    to={`/traces?trace_ids=${standout.trace_ids.slice(0, 200).join(",")}`}
                  >
                    <span className={`standout-lift ${standout.kind}`}>
                      {standout.kind === "failure" || standout.kind === "never_resolved"
                        ? `${standout.count}`
                        : `${standout.lift.toFixed(1)}×`}
                    </span>
                    <span className="standout-body">
                      <strong>{standout.label}</strong>
                      <span>{standout.text}</span>
                    </span>
                  </Link>
                ))}
              </div>
            </section>
          )}

          <section className="groups-section">
            <div className="section-heading">
              <h2>{run.group_by_label ? `By ${run.group_by_label.toLowerCase()}` : "The groups"}</h2>
              <span>{answer.unit === "count" ? "share of the answer set" : `${answer.metric} · ${answer.unit}`}</span>
            </div>
            <Bars
              rows={answer.groups.map((group) => ({
                key: group.key,
                label: group.label,
                value: answer.unit === "count" ? group.count : Number(group.value.toFixed(6)),
                caption: answer.unit === "count" ? `${(group.share * 100).toFixed(1)}%` : group.value_label,
              }))}
              onSelect={(key) => {
                const group = answer.groups.find((item) => item.key === key);
                if (group) navigate(`/traces?trace_ids=${group.trace_ids.slice(0, 200).join(",")}`);
              }}
            />
          </section>

          <section className="evidence-section">
            <div className="section-heading">
              <h2>Inside each group</h2>
              <span>Every quote is a literal substring of a stored redacted span</span>
            </div>
            <div className="group-list">
              {realGroups.slice(0, 6).map((group, index) => (
                <GroupCard
                  key={group.key}
                  group={group}
                  rank={index + 1}
                  showValue={answer.unit !== "count"}
                />
              ))}
            </div>
            {!answer.evidence.length && (
              <div className="empty-state small">
                No quote survived verbatim verification against the redacted spans, so none is shown.
              </div>
            )}
          </section>

          {answer.aspect_id && <Verification aspectId={answer.aspect_id} />}

          <section className="work-section">
            <button className="section-toggle" type="button" onClick={() => setShowWork((value) => !value)} aria-expanded={showWork}>
              <span>
                <strong>Show the work</strong>
                <small>Selection, method, executed code, and raw stdout</small>
              </span>
              <ChevronRight className={showWork ? "rotate" : ""} size={17} />
            </button>
            {showWork && (
              <div className="work-body">
                <div className="work-stats">
                  <span><Sigma size={12} /> {answer.work.execution.replaceAll("_", " ")}</span>
                  <span>{answer.work.input_row_count.toLocaleString()} input rows</span>
                  <span>{answer.work.denominator.toLocaleString()} denominator</span>
                  <span>{answer.work.excluded_count.toLocaleString()} excluded</span>
                  {answer.work.request_id && <span className="mono">{answer.work.request_id}</span>}
                </div>
                <ul className="method-notes">
                  {answer.work.method_notes.map((note) => <li key={note}>{note}</li>)}
                </ul>
                {answer.work.sql && <CodeBlock language="selection.sql" code={formatSql(answer.work.sql, answer.work.sql_params)} />}
                <CodeBlock language="aggregation.py" code={answer.work.code} />
                <CodeBlock language="stdout.json" code={answer.work.stdout_json} />
              </div>
            )}
          </section>

          <section className="followup-section">
            <div className="section-heading">
              <h2>Keep going</h2>
              <span>Follow-ups inherit this question's scope</span>
            </div>
            <div className="chip-row left">
              {[...answer.follow_ups, ...(answer.is_overview ? [] : answer.suggestions.slice(0, 2))].map((suggestion) => (
                <button key={suggestion} type="button" className="chip" onClick={() => ask.mutate(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
            <form
              className="followup-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (followUp.trim()) ask.mutate(followUp.trim());
              }}
            >
              <input
                value={followUp}
                onChange={(event) => setFollowUp(event.target.value)}
                placeholder="Ask a follow-up, e.g. “now only the checkout agent”"
                aria-label="Follow-up question"
              />
              <button className="primary-button" type="submit" disabled={ask.isPending || followUp.trim().length < 3}>
                {ask.isPending ? "Planning…" : "Ask"}
              </button>
            </form>
            {ask.error && <div className="inline-error" role="alert">{(ask.error as Error).message}</div>}
          </section>
        </>
      )}
    </div>
  );
}

function formatSql(sql: string, params: string[]): string {
  const pretty = sql
    .replace(/ FROM /g, "\nFROM ")
    .replace(/ WHERE /g, "\nWHERE ")
    .replace(/ AND /g, "\n  AND ")
    .replace(/ ORDER BY /g, "\nORDER BY ")
    .replace(/ JOIN /g, "\nJOIN ");
  return params.length ? `${pretty}\n\n-- parameters: ${JSON.stringify(params)}` : pretty;
}

function Verification({ aspectId }: { aspectId: string }) {
  const verification = useQuery({ queryKey: ["verification", aspectId], queryFn: () => api.verification(aspectId) });
  const data = verification.data;
  if (!data) return null;
  const local = String(data.judge ?? "").startsWith("local:");
  return (
    <section className="verification-section">
      <div className="section-heading">
        <h2>Check the aspect</h2>
        <span>Five yes and five no, highest confidence first</span>
      </div>
      {local && (
        <div className="notice">
          <TriangleAlert size={15} />
          <div>
            <strong>Judged locally by <span className="mono">{data.judge}</span>, not by a language model.</strong>
            <p>
              It scores each conversation on term overlap and latent-space similarity to the aspect question. That is
              accurate for questions the conversations state in their own words and weaker for paraphrases. Read the
              samples below; if they are wrong, edit the question and rerun — a changed question becomes a new aspect
              version rather than overwriting this evidence.
            </p>
          </div>
        </div>
      )}
      <div className="verification-grid">
        {(["yes", "no"] as const).map((key) => (
          <div key={key}>
            <h3>{key === "yes" ? "Marked yes" : "Marked no"}</h3>
            {(data[key] ?? []).map((row: any) => (
              <Link key={row.trace_id} to={`/traces/${row.trace_id}`}>
                <span className="mono">{row.trace_id}</span>
                <q>{row.evidence_quote || row.user_request}</q>
                <small>confidence {Number(row.confidence).toFixed(2)}</small>
              </Link>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
