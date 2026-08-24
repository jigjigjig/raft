export type CompiledFilter = { field: string; label: string };

export type QuestionPlan = {
  question: string;
  path: "layer1" | "layer2_cluster" | "layer3_aspect";
  explanation: string;
  requires_confirmation: boolean;
  aspect?: { question: string; type: string; created_from: string };
  estimate_usd: number;
  estimate_seconds_min: number;
  estimate_seconds_max: number;
  eligible_count: number;
  total_count: number;
  planner_mode: "local" | "otari_mcp";
  metric: string;
  aggregate: string;
  group_by?: string | null;
  group_by_label?: string | null;
  filters: CompiledFilter[];
  focus_terms: string[];
  rationale: string[];
  plan_summary: string;
  spec: Record<string, unknown>;
  parent_run_id?: string | null;
};

export type AnalysisRun = {
  id: string;
  question: string;
  path: string;
  status: string;
  aspect_id?: string;
  aspect?: { question: string; type: string; version: number } | null;
  estimate_usd: number;
  estimate_seconds_min: number;
  estimate_seconds_max: number;
  completed: number;
  total: number;
  message: string;
  answer_id?: string;
  error?: string;
  planner_note: string;
  parent_run_id?: string | null;
  filters: string[];
  rationale: string[];
  group_by_label?: string | null;
  metric_label?: string | null;
};

export type GroupProfile = {
  resolved: number;
  resolved_share: number;
  gave_up: number;
  gave_up_share: number;
  frustrated: number;
  cost_usd: number;
  avg_turns: number;
  avg_rephrases: number;
  top_failure?: string | null;
  top_failure_count: number;
  top_outcome?: string | null;
  top_outcome_count: number;
  apps: string[];
  tools: string[];
};

export type AnswerGroup = {
  key: string;
  label: string;
  count: number;
  share: number;
  trace_ids: string[];
  value: number;
  value_label: string;
  terms: string[];
  exemplar?: string | null;
  quotes: string[];
  profile?: GroupProfile | null;
};

export type Standout = {
  kind: string;
  group_key: string;
  label: string;
  text: string;
  lift: number;
  count: number;
  trace_ids: string[];
};

export type Answer = {
  id: string;
  run_id: string;
  question: string;
  path: string;
  denominator: number;
  excluded_count: number;
  groups: AnswerGroup[];
  interpretation: string;
  headline: string;
  metric: string;
  unit: string;
  follow_ups: string[];
  standouts: Standout[];
  evidence: Array<{
    trace_id: string;
    group_key: string;
    label: string;
    quote: string;
    speaker: string;
  }>;
  work: {
    execution: string;
    code: string;
    input_row_count: number;
    denominator: number;
    excluded_count: number;
    stdout_json: string;
    session_id?: string;
    request_id?: string;
    sql: string;
    sql_params: string[];
    method_notes: string[];
  };
  aspect_id?: string;
};

export type Span = {
  id: string;
  index: number;
  type: string;
  name?: string;
  duration_ms?: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  status: string;
  content_redacted: string;
  error_code?: string;
  repeat_count: number;
};

export type Trace = {
  id: string;
  app: string;
  started_at: string;
  duration_ms?: number;
  model: string;
  provider: string;
  status: string;
  capture_completeness: string;
  cost_usd: number;
  tokens_input: number;
  tokens_output: number;
  outcome: string;
  failure_mode: string;
  summary: string;
  user_request: string;
  what_happened: string;
  verbatim_quote: string;
  autopsy: string;
  redaction_status: string;
  guardrail_status: string;
  intent_label: string;
  intent_satisfied: string;
  sentiment_end: string;
  ended_by: string;
  user_gave_up: boolean;
  turns: number;
  tool_calls: number;
  distinct_tools: string[];
  repeated_identical_calls: number;
  rephrase_count: number;
  error_code?: string | null;
  spans: Span[];
};

export type TraceListItem = Trace & { relevance?: number };
