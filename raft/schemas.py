from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


FailureMode = Literal[
    "none",
    "tool_error",
    "tool_loop",
    "hallucinated_tool",
    "context_overflow",
    "refusal",
    "wrong_answer",
    "timeout",
    "guardrail_block",
    "provider_error",
]

RunStatus = Literal[
    "queued",
    "planning",
    "awaiting_confirmation",
    "evaluating",
    "aggregating",
    "synthesizing",
    "complete",
    "paused_budget",
    "paused_provider",
    "guardrail_blocked",
    "failed",
]


class TraceLabel(BaseModel):
    intent_satisfied: Literal["yes", "no", "unclear"]
    user_gave_up: bool
    rephrase_count: int = Field(ge=0, le=50)
    sentiment_end: Literal["positive", "neutral", "frustrated", "angry", "unclear"]
    failure_mode: FailureMode
    confidence: float = Field(ge=0, le=1)
    user_request: str = Field(min_length=3, max_length=600)
    what_happened: str = Field(min_length=3, max_length=1000)
    verbatim_quote: str = Field(min_length=1, max_length=500)


class Span(BaseModel):
    id: str
    index: int
    type: str
    name: str | None = None
    duration_ms: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0
    status: str = "ok"
    content_redacted: str
    error_code: str | None = None


class Trace(BaseModel):
    id: str
    app: str
    started_at: str
    duration_ms: int | None = None
    model: str
    provider: str
    status: str
    capture_completeness: str
    cost_usd: float
    tokens_input: int
    tokens_output: int
    outcome: str
    failure_mode: FailureMode
    summary: str
    user_request: str
    what_happened: str
    verbatim_quote: str
    redaction_status: str
    guardrail_status: str
    autopsy: str
    spans: list[Span] = []


class AspectDefinition(BaseModel):
    id: str | None = None
    question: str
    type: Literal["boolean", "number", "category", "text"]
    created_from: str
    version: int = 1
    # Closed answer set for a category aspect, so values can be grouped.
    labels: list[str] = Field(default_factory=list)


class CompiledFilter(BaseModel):
    field: str
    label: str


class QuestionPlan(BaseModel):
    question: str
    path: Literal["layer1", "layer2_cluster", "layer3_aspect"]
    explanation: str
    requires_confirmation: bool = False
    aspect: AspectDefinition | None = None
    estimate_usd: float = 0
    estimate_seconds_min: int = 0
    estimate_seconds_max: int = 0
    eligible_count: int = 0
    total_count: int = 0
    planner_mode: Literal["local", "otari_mcp"] = "local"
    inspected_tools: list[str] = Field(default_factory=list)
    # What the compiler understood, shown to the user before anything runs.
    metric: str = "traces"
    aggregate: str = "count"
    group_by: str | None = None
    group_by_label: str | None = None
    filters: list[CompiledFilter] = Field(default_factory=list)
    focus_terms: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    plan_summary: str = ""
    spec: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: str | None = None


class CreateRunRequest(BaseModel):
    question: str
    plan: QuestionPlan
    parent_run_id: str | None = None


class ConfirmRunRequest(BaseModel):
    aspect_question: str | None = None


class AnalysisRun(BaseModel):
    id: str
    question: str
    path: str
    parent_run_id: str | None = None
    status: RunStatus
    aspect_id: str | None = None
    estimate_usd: float
    estimate_seconds_min: int
    estimate_seconds_max: int
    completed: int
    total: int
    message: str
    answer_id: str | None = None
    error: str | None = None
    planner_note: str = ""
    updated_at: str


class GroupProfile(BaseModel):
    """What actually happened to the conversations in one group."""

    resolved: int = 0
    resolved_share: float = 0
    gave_up: int = 0
    gave_up_share: float = 0
    frustrated: int = 0
    cost_usd: float = 0
    avg_turns: float = 0
    avg_rephrases: float = 0
    top_failure: str | None = None
    top_failure_count: int = 0
    top_outcome: str | None = None
    top_outcome_count: int = 0
    top_app: str | None = None
    top_app_count: int = 0
    apps: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class Standout(BaseModel):
    """A group that behaves unlike the rest of the answer set."""

    kind: str
    group_key: str
    label: str
    text: str
    lift: float
    count: int
    trace_ids: list[str] = Field(default_factory=list)


class AnswerGroup(BaseModel):
    key: str
    label: str
    count: int
    share: float
    trace_ids: list[str]
    value: float = 0
    value_label: str = ""
    terms: list[str] = Field(default_factory=list)
    exemplar: str | None = None
    quotes: list[str] = Field(default_factory=list)
    profile: GroupProfile | None = None
    # True when the question already filtered on resolution, so restating it
    # would report the filter back to the user as a finding.
    resolution_is_implied: bool = False


class EvidenceQuote(BaseModel):
    trace_id: str
    group_key: str
    label: str
    quote: str
    span_index: int | None = None
    speaker: str = "user"


class WorkRecord(BaseModel):
    execution: Literal["otari_code_execution", "local_reference"]
    code: str
    input_row_count: int
    denominator: int
    excluded_count: int
    stdout_json: str
    session_id: str | None = None
    request_id: str | None = None
    sql: str = ""
    sql_params: list[Any] = Field(default_factory=list)
    method_notes: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    id: str
    run_id: str
    question: str
    path: str
    denominator: int
    excluded_count: int
    groups: list[AnswerGroup]
    interpretation: str
    evidence: list[EvidenceQuote]
    work: WorkRecord
    aspect_id: str | None = None
    headline: str = ""
    metric: str = "traces"
    unit: str = "count"
    spec: dict[str, Any] = Field(default_factory=dict)
    follow_ups: list[str] = Field(default_factory=list)
    standouts: list[Standout] = Field(default_factory=list)
    # True when the question named nothing Raft could tie to these
    # conversations, so this is the shape of the dataset rather than an answer
    # to what was asked.
    is_overview: bool = False
    suggestions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_denominator(self) -> "Answer":
        if sum(group.count for group in self.groups) != self.denominator:
            raise ValueError("answer groups must sum to the denominator")
        return self


class ModelObservation(BaseModel):
    role: str
    requested_model: str
    final_model: str | None = None
    provider: str | None = None
    request_id: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    status: str
    error: str | None = None
    created_at: str


class CompletionResult(BaseModel):
    content: str
    request_id: str | None = None
    model: str | None = None
    provider: str | None = None
    usage: dict[str, Any] = {}
    raw: dict[str, Any] = {}
