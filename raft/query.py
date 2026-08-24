"""Question compiler.

Journey 2 promises that a person can type anything. That only works if the
question is actually read, so this module turns free text into a typed
`QuerySpec` - metric, grouping, filters and a residual semantic focus - and then
into parameterised SQL plus the exact Python that will compute the numbers.

Nothing here guesses an answer. The compiler only decides *what to compute*; the
counting happens in `AGGREGATION` code executed over real rows. When a question
contains meaning the shape fields cannot express, the residual focus survives
compilation and routes the question to clustering or to a new aspect instead of
being silently dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    sql: str
    plural: str
    synonyms: tuple[str, ...]


DIMENSIONS: dict[str, Dimension] = {
    "app": Dimension("app", "App", "t.app", "apps", ("app", "application", "agent", "bot", "assistant", "product", "surface")),
    "outcome": Dimension("outcome", "Outcome", "t.outcome", "outcomes", ("outcome", "result", "how it ended", "ending")),
    "failure_mode": Dimension(
        "failure_mode", "Failure mode", "t.failure_mode", "failure modes",
        ("failure mode", "failure", "failing", "fails", "breaks", "breaking", "broken", "goes wrong", "went wrong", "error type", "bug"),
    ),
    "model": Dimension("model", "Model", "t.model", "models", ("model", "llm", "checkpoint")),
    "provider": Dimension("provider", "Provider", "t.provider", "providers", ("provider", "vendor", "upstream")),
    "status": Dimension("status", "Status", "t.status", "statuses", ("status",)),
    "sentiment_end": Dimension(
        "sentiment_end", "Ending sentiment", "t.sentiment_end", "ending sentiments",
        ("sentiment", "mood", "tone", "feeling", "how they felt"),
    ),
    "intent_satisfied": Dimension(
        "intent_satisfied", "Intent satisfied", "t.intent_satisfied", "satisfaction states",
        ("satisfied", "satisfaction", "got what they wanted", "intent satisfied"),
    ),
    "ended_by": Dimension("ended_by", "Ended by", "t.ended_by", "enders", ("ended by", "who ended", "ends")),
    "error_code": Dimension("error_code", "Error code", "t.error_code", "error codes", ("error code", "error codes", "provider error")),
    "intent_key": Dimension("intent_key", "Detected intent", "t.intent_key", "intents", ("intent", "request type", "kind of request")),
    "tool": Dimension("tool", "Tool", "tt.tool", "tools", ("tool", "tools", "function call", "tool call", "tool calls")),
    # "Which conversation cost the most" wants the conversations themselves, not
    # a breakdown of them by some other column.
    "trace": Dimension(
        "trace", "Conversation", "t.id", "conversations",
        ("conversation", "conversations", "trace", "traces", "chat", "chats", "session", "sessions"),
    ),
    "day": Dimension("day", "Day", "substr(t.started_at,1,10)", "days", ("day", "daily", "by date", "per day")),
    "week": Dimension("week", "Week", "strftime('%Y-W%W', t.started_at)", "weeks", ("week", "weekly", "per week", "week over week")),
    "hour": Dimension("hour", "Hour", "substr(t.started_at,12,2)", "hours", ("hour", "hourly", "time of day")),
    "capture_completeness": Dimension(
        "capture_completeness", "Capture completeness", "t.capture_completeness", "capture states", ("capture", "completeness", "incomplete capture"),
    ),
    "guardrail_status": Dimension("guardrail_status", "Guardrail status", "t.guardrail_status", "guardrail states", ("guardrail",)),
}


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    sql: str
    unit: Literal["count", "usd", "tokens", "ms", "plain"]
    synonyms: tuple[str, ...]


METRICS: dict[str, Metric] = {
    "traces": Metric("traces", "Conversations", "1", "count", ("conversation", "conversations", "trace", "traces", "chat", "chats", "session", "sessions", "people", "users")),
    "cost": Metric("cost", "Spend", "t.cost_usd", "usd", ("cost", "costs", "costing", "spend", "spending", "spent", "expensive", "price", "pricey", "dollar", "money", "budget", "bill", "pay", "paying", "paid", "$")),
    "tokens": Metric("tokens", "Tokens", "(t.tokens_input + t.tokens_output)", "tokens", ("token", "tokens", "context size")),
    "duration": Metric("duration", "Duration", "t.duration_ms", "ms", ("duration", "latency", "slow", "slowest", "fast", "long", "took", "seconds", "response time", "time taken")),
    "turns": Metric("turns", "Turns", "t.turns", "plain", ("turn", "turns", "back and forth", "messages")),
    "tool_calls": Metric("tool_calls", "Tool calls", "t.tool_calls", "plain", ("tool call", "tool calls", "function calls")),
    "rephrases": Metric("rephrases", "Rephrases", "t.rephrase_count", "plain", ("rephrase", "rephrases", "rephrasing", "repeat themselves", "ask again")),
    "repeats": Metric("repeats", "Repeated identical calls", "t.repeated_identical_calls", "plain", ("repeated call", "repeated calls", "identical call", "identical calls", "repeated tool call")),
}


# ---------------------------------------------------------------------------
# Value lexicon: phrases that pin a filter without naming the column
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Predicate:
    field: str
    sql: str
    params: tuple[Any, ...]
    label: str
    id: str = ""


def _pred(field_key: str, sql: str, params: tuple[Any, ...], label: str, id: str = "") -> Predicate:
    return Predicate(field_key, sql, params, label, id)


# Each entry: (regex, predicate factory, consumes -> phrases removed from focus)
LEXICON: list[tuple[re.Pattern[str], Predicate]] = [
    (re.compile(r"\bgave up\b|\bgive up\b|\bgiving up\b|\bgives up\b|\babandon\w*\b|\bwalked away\b|\bdropped out\b|\bbailed\b"),
     _pred("user_gave_up", "t.user_gave_up = 1", (), "the user gave up", id="the_user_gave_up")),
    (re.compile(r"\bfrustrat\w*\b|\bangry\b|\bannoyed\b|\bupset\b|\bmad\b|\birritat\w*\b"),
     _pred("sentiment_end", "t.sentiment_end IN ('frustrated','angry')", (), "the user ended frustrated or angry", id="the_user_ended_frustrated_or_angry")),
    (re.compile(r"\bhappy\b|\bsatisfied customers?\b|\bpleased\b|\bthankful\b|\bgrateful\b"),
     _pred("sentiment_end", "t.sentiment_end = 'positive'", (), "the user ended positively", id="the_user_ended_positively")),
    (re.compile(
        r"\bunresolved\b|\bnot resolved\b|\bunsuccessful\b|\bdidn'?t get\b|\bdid not get\b|\bnever got\b|"
        r"\bwithout (?:\w+\s+){0,3}(?:getting|receiving)\b|\bnot satisfied\b|\bunsatisf\w*\b|\bwent unanswered\b|"
        r"\bempty[- ]handed\b|\bno answer\b|\bwithout an answer\b|\bstruggl\w*\b|\bhaving trouble\b|\bpain ?points?\b"),
     _pred("intent_satisfied", "t.intent_satisfied = 'no'", (), "the user did not get what they wanted", id="the_user_did_not_get_what_they_wanted")),
    (re.compile(r"\bresolved\b|\bsuccessful\b|\bsucceeded\b|\bworked (?:well|fine)\b|\bwent well\b|\bgot what they wanted\b|\bhappy path\b"),
     _pred("intent_satisfied", "t.intent_satisfied = 'yes'", (), "the user got what they wanted", id="the_user_got_what_they_wanted")),
    (re.compile(r"\bfail\w*\b|\bbroke\w*\b|\bbreaks?\b|\bwent wrong\b|\bgoes wrong\b|\bproblem\w*\b|\bissues?\b"),
     _pred("failure_mode", "t.failure_mode != 'none'", (), "something went wrong", id="something_went_wrong")),
    (re.compile(r"\bloop\w*\b|\bretry\b|\bretries\b|\bretried\b|\brepeat\w* (?:the )?(?:same )?(?:tool|call)\w*\b|\bsame call\b|\bover and over\b|\bin circles\b"),
     _pred("failure_mode", "t.failure_mode = 'tool_loop'", (), "the agent looped on one tool", id="the_agent_looped_on_one_tool")),
    (re.compile(r"\bhallucinat\w*\b|\bmade up\b|\bmakes up\b|\bmaking up\b|\binvent\w*\b|\bfabricat\w*\b|\bnot true\b|\bwrong information\b"),
     _pred("failure_mode", "t.failure_mode IN ('wrong_answer','hallucinated_tool') OR t.outcome = 'hallucination'", (), "the agent stated something unverified", id="the_agent_stated_something_unverified")),
    (re.compile(r"\bclaimed to\b|\bpretend\w*\b|\bfake success\b|\bsaid it did\b|\bphantom\b|\bhallucinated tool\b"),
     _pred("failure_mode", "t.failure_mode = 'hallucinated_tool'", (), "the agent claimed an action it never performed", id="the_agent_claimed_an_action_it_never_per")),
    (re.compile(r"\brefus\w*\b|\bdeclin\w*\b|\bwould'?n?'?t help\b|\bwont help\b|\bblocked the user\b"),
     _pred("failure_mode", "t.failure_mode = 'refusal'", (), "the agent refused", id="the_agent_refused")),
    (re.compile(r"\btime(?:d)? ?out\b|\btimeouts?\b"),
     _pred("failure_mode", "t.failure_mode = 'timeout'", (), "the request timed out", id="the_request_timed_out")),
    (re.compile(r"\bcontext (?:window|length|overflow)\b|\btoo (?:long|large) (?:for )?context\b|\bran out of context\b"),
     _pred("failure_mode", "t.failure_mode = 'context_overflow'", (), "the context window was exceeded", id="the_context_window_was_exceeded")),
    (re.compile(r"\bprovider error\w*\b|\bupstream error\w*\b|\b5\d\d\b|\bgateway error\b|\boverloaded\b"),
     _pred("failure_mode", "t.failure_mode = 'provider_error'", (), "an upstream provider error occurred", id="an_upstream_provider_error_occurred")),
    (re.compile(r"\btool error\w*\b|\btool failed\b|\btool failures?\b"),
     _pred("failure_mode", "t.failure_mode = 'tool_error'", (), "a tool call failed", id="a_tool_call_failed")),
    (re.compile(r"\bwrong (?:action|answer|thing)\b|\bmisunderstood\b|\bmisread\b|\banswered the wrong\b"),
     _pred("failure_mode", "t.failure_mode = 'wrong_answer'", (), "the agent answered the wrong question", id="the_agent_answered_the_wrong_question")),
    (re.compile(r"\bcan'?not do\b|\bcan'?t do\b|\bcannot handle\b|\bcan'?t handle\b|\bunable to\b|\bunsupported\b|\bnot supported\b|\bmissing (?:feature|capability|tool)\b|\bunmet\b|\basking for that (?:it|my agent) can'?t\b|\bout of scope\b|\bdoesn'?t support\b"),
     _pred("outcome", "t.outcome = 'unmet request'", (), "the request was outside the agent's capabilities", id="the_request_was_outside_the_agent_s_capa")),
    (re.compile(r"\bcontradict\w*\b|\bcontradiction\b|\bboth (?:said|claimed)\b"),
     _pred("outcome", "t.outcome = 'contradiction'", (), "the agent contradicted itself", id="the_agent_contradicted_itself")),
    (re.compile(r"\bincomplete captures?\b|\bpartial captures?\b|\bdisconnect\w*\b"),
     _pred("capture_completeness", "t.capture_completeness != 'complete'", (), "the capture is incomplete", id="the_capture_is_incomplete")),
    (re.compile(r"\bguardrail(?:ed| blocked| block)?\b|\bquarantin\w*\b"),
     _pred("guardrail_status", "t.guardrail_status = 'blocked'", (), "the trace was guardrail-blocked", id="the_trace_was_guardrail_blocked")),
    (re.compile(r"\bmulti[- ]?turn\b|\blong conversations?\b"),
     _pred("turns", "t.turns >= 4", (), "the conversation ran four or more turns", id="the_conversation_ran_four_or_more_turns")),
]


# Phrases that unambiguously ask about the content of conversations rather than
# about a recorded column. Anything vaguer is decided by sentence shape below.
CONTENT_MARKERS = re.compile(
    r"\bstruggl\w*\b|\bpain ?points?\b|\bcomplain\w*\b|\bthemes?\b|\btopics?\b|\bpatterns?\b|"
    r"\bcategor\w*\b|\bcluster\w*\b|\bsubjects?\b|\bgroup (?:them|these|the conversations)\b|"
    r"\bwhat (?:kind|sort|type)s? of\b|\bask\w*\b(?=[^?]*\b(?:for|about|regarding)\b)|"
    r"\btalk\w* about\b|\bbring(?:ing)? up\b|\bin their own words\b|\bwhat are people\b|"
    r"\bwhat do (?:my |our |the )?(?:users|people|customers|they)\b|"
    r"^\s*what (?:are|is|do|does|did)\b[^?]*\babout\b",
    re.IGNORECASE,
)

# "What ... ?" with meaning left over after compilation is an open-ended
# question about content, not a request for a column.
OPEN_ENDED_LEAD = re.compile(r"^\s*(?:what|which things|which kinds?|which sorts?|which types?)\b", re.IGNORECASE)

COUNTING_MARKERS = re.compile(
    r"\bhow many\b|\bhow much\b|\bhow often\b|\bwhat (?:share|percentage|percent|proportion|fraction)\b|"
    r"\bcount\b|\bnumber of\b|\bhow frequently\b",
    re.IGNORECASE,
)

RANK_MARKERS = re.compile(
    r"\bmost\b|\btop\b|\bbiggest\b|\blargest\b|\bhighest\b|\bworst\b|\bleast\b|\bsmallest\b|\brank\w*\b|"
    r"\bwhich\b|\bwhere (?:am|are|do|does|is)\b|\bbreak ?down\b|\bby \w+\b",
    re.IGNORECASE,
)

AVERAGE_MARKERS = re.compile(r"\baverage\b|\bavg\b|\bmean\b|\btypical\b|\bmedian\b|\bper (?:trace|conversation|chat)\b", re.IGNORECASE)

TREND_MARKERS = re.compile(r"\bover time\b|\btrend\w*\b|\bday by day\b|\bweek over week\b|\bdaily\b|\bweekly\b|\beach day\b|\bper day\b|\bper week\b", re.IGNORECASE)

YES_NO_MARKERS = re.compile(r"^\s*(?:did|do|does|is|are|was|were|has|have|can|could|should|would|will)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


@dataclass
class QuerySpec:
    question: str
    path: Literal["layer1", "layer2_cluster", "layer3_aspect"] = "layer1"
    metric: str = "traces"
    aggregate: Literal["count", "sum", "avg", "max"] = "count"
    group_by: str | None = None
    predicates: list[Predicate] = field(default_factory=list)
    focus: str = ""
    focus_terms: list[str] = field(default_factory=list)
    limit: int = 8
    aspect_question: str | None = None
    aspect_type: str = "boolean"
    rationale: list[str] = field(default_factory=list)
    matched_phrases: list[str] = field(default_factory=list)
    sort: Literal["value", "key"] = "value"
    # Set when nothing in the question could be tied to the data, so the answer
    # is an overview and must not be presented as a precise reply.
    broad: bool = False

    # -- SQL --------------------------------------------------------------
    def where(self) -> tuple[str, list[Any]]:
        if not self.predicates:
            return "", []
        clauses = []
        params: list[Any] = []
        for predicate in self.predicates:
            clauses.append(f"({predicate.sql})")
            params.extend(predicate.params)
        return " AND ".join(clauses), params

    @property
    def needs_tool_join(self) -> bool:
        return self.group_by == "tool" or any(item.field == "tool" for item in self.predicates)

    @property
    def metric_definition(self) -> Metric:
        return METRICS[self.metric]

    @property
    def group_dimension(self) -> Dimension | None:
        return DIMENSIONS.get(self.group_by) if self.group_by else None

    def filter_summary(self) -> str:
        """Reads as a noun phrase, so it can follow "Of 847 …" in a sentence."""
        if not self.predicates:
            return "captured conversations"
        return "conversations where " + " and ".join(item.label for item in self.predicates)

    def group_dimension_is_explicit(self) -> bool:
        """True when the user named the grouping rather than Raft choosing it."""
        dimension = self.group_dimension
        if not dimension:
            return False
        lowered = self.question.casefold()
        # Word boundaries matter: "developer assistant" contains "per assistant".
        return any(
            re.search(rf"\b{lead} {re.escape(synonym)}\b", lowered)
            for synonym in dimension.synonyms
            for lead in ("by", "per", "which", "each", "group by")
        )

    def describe(self) -> str:
        metric = self.metric_definition
        if self.aggregate == "count":
            head = "count conversations"
        elif self.aggregate == "avg":
            head = f"average {metric.label.lower()} per conversation"
        elif self.aggregate == "max":
            head = f"highest {metric.label.lower()}"
        else:
            head = f"total {metric.label.lower()}"
        group = self.group_dimension
        tail = f" grouped by {group.label.lower()}" if group else ""
        return f"{head}{tail} over {self.filter_summary()}"


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


class QuestionCompiler:
    def __init__(self, catalog: "DatasetCatalog"):
        self.catalog = catalog

    def compile(self, question: str) -> QuerySpec:
        text = question.strip()
        normalized = f" {re.sub(r'[^a-z0-9$%. -]+', ' ', text.casefold())} "
        spec = QuerySpec(question=text)
        consumed_spans: list[tuple[int, int]] = []

        # 1. Concrete catalog values (app names, tools, models, error codes).
        for value, predicate, span in self.catalog.match(normalized):
            if not any(item.sql == predicate.sql for item in spec.predicates):
                spec.predicates.append(predicate)
                spec.matched_phrases.append(value)
            consumed_spans.append(span)

        # 2. Lexicon phrases.
        for pattern, predicate in LEXICON:
            match = pattern.search(normalized)
            if not match:
                continue
            if any(item.sql == predicate.sql for item in spec.predicates):
                consumed_spans.append(match.span())
                continue
            # A dimension the user wants grouped should not also become a filter.
            spec.predicates.append(predicate)
            spec.matched_phrases.append(match.group(0).strip())
            consumed_spans.append(match.span())

        # 3. Time window.
        window = self._time_window(normalized)
        if window:
            predicate, phrase, span = window
            spec.predicates.append(predicate)
            spec.matched_phrases.append(phrase)
            consumed_spans.append(span)

        # 4. Metric and aggregate.
        metric_key, metric_span = self._metric(normalized)
        if metric_span:
            consumed_spans.append(metric_span)
        spec.metric = metric_key
        average = AVERAGE_MARKERS.search(normalized)
        if average:
            consumed_spans.append(average.span())
        if metric_key == "traces":
            spec.aggregate = "count"
        elif average:
            spec.aggregate = "avg"
        elif metric_key == "duration" and not re.search(r"\btotal\b|\ball together\b|\bcombined\b", normalized):
            # Ranking apps or models by *total* latency just ranks them by volume.
            spec.aggregate = "avg"
        else:
            spec.aggregate = "sum"

        # 5. Group-by.
        group_key, group_span = self._group_by(normalized, spec)
        if group_span:
            consumed_spans.append(group_span)
        spec.group_by = group_key

        # 6. Residual focus - everything the compiler could not account for.
        spec.focus = _residual(normalized, consumed_spans)
        spec.focus_terms = [word for word in spec.focus.split() if len(word) > 2]

        # 7. Path.
        self._route(spec, normalized)
        self._explain(spec, normalized)
        return spec

    # -- pieces -----------------------------------------------------------
    def _metric(self, normalized: str) -> tuple[str, tuple[int, int] | None]:
        best: tuple[int, str, tuple[int, int]] | None = None
        for key, metric in METRICS.items():
            if key == "traces":
                continue
            for synonym in metric.synonyms:
                # Whole words only: "the billing bot" is an app, not a question
                # about spend, and a substring match would read it as one.
                match = re.search(rf"(?<![a-z0-9]){re.escape(synonym)}(?![a-z0-9])", normalized)
                if match:
                    candidate = (len(synonym), key, match.span())
                    if best is None or candidate[0] > best[0]:
                        best = candidate
        if best:
            return best[1], best[2]
        return "traces", None

    def _group_by(self, normalized: str, spec: QuerySpec) -> tuple[str | None, tuple[int, int] | None]:
        individual = re.search(
            r"\bwhich (?:conversation|trace|chat|session)s?\b|\btop \d+ (?:conversation|trace|chat)s?\b",
            normalized,
        )
        if individual and spec.metric != "traces":
            return "trace", individual.span()
        if TREND_MARKERS.search(normalized):
            match = TREND_MARKERS.search(normalized)
            key = "week" if re.search(r"\bweek", normalized) else "day"
            return key, match.span() if match else None

        best: tuple[int, str, tuple[int, int]] | None = None
        for key, dimension in DIMENSIONS.items():
            if key == "trace":
                # Only the explicit "which conversation…" form above selects
                # this; otherwise the word "conversations" in any question would
                # turn the answer into one group per row.
                continue
            for synonym in dimension.synonyms:
                for pattern in (f" by {synonym}", f" per {synonym}", f" which {synonym}", f" each {synonym}", f" {synonym}"):
                    position = normalized.find(pattern)
                    if position >= 0:
                        weight = len(pattern) + (30 if pattern.startswith((" by ", " per ", " which ", " each ")) else 0)
                        candidate = (weight, key, (position, position + len(pattern)))
                        if best is None or candidate[0] > best[0]:
                            best = candidate
        # Grouping by a field the question already pins to one value produces a
        # single 100% group, which tells the reader nothing.
        pinned = {item.field for item in spec.predicates}
        if best and best[1] in pinned and best[0] < 30:
            best = None
        if best and best[0] >= 30:
            return best[1], best[2]

        # No explicit grouping. Pick the dimension that makes the answer useful.
        if RANK_MARKERS.search(normalized) or spec.metric != "traces":
            if best:
                return best[1], best[2]
            filtered = {item.field for item in spec.predicates}
            for candidate in ("failure_mode", "outcome", "app"):
                if candidate not in filtered:
                    return candidate, None
            return "app", None
        if best:
            return best[1], best[2]
        return None, None

    def _time_window(self, normalized: str) -> tuple[Predicate, str, tuple[int, int]] | None:
        now = datetime.now(UTC)
        patterns: list[tuple[re.Pattern[str], Any]] = [
            (re.compile(r"\b(?:in the )?(?:last|past|previous)\s+(\d+)\s+(hour|day|week|month)s?\b"), None),
            (re.compile(r"\b(?:this|the past)\s+(week)\b"), 7),
            (re.compile(r"\b(?:this|the past)\s+(month)\b"), 30),
            (re.compile(r"\byesterday\b"), 2),
            (re.compile(r"\btoday\b"), 1),
            (re.compile(r"\brecent(?:ly)?\b"), 7),
        ]
        for pattern, fixed_days in patterns:
            match = pattern.search(normalized)
            if not match:
                continue
            if fixed_days is None:
                amount = int(match.group(1))
                unit = match.group(2)
                hours = {"hour": 1, "day": 24, "week": 168, "month": 720}[unit] * amount
            else:
                hours = fixed_days * 24
            cutoff = (now - timedelta(hours=hours)).isoformat()
            phrase = match.group(0).strip()
            return (
                _pred("started_at", "t.started_at >= ?", (cutoff,), f"it started within {phrase}", id="time_window"),
                phrase,
                match.span(),
            )
        return None

    def _route(self, spec: QuerySpec, normalized: str) -> None:
        focus = spec.focus_terms
        meaningful_focus = [word for word in focus if word not in _FILLER]
        content_question = bool(CONTENT_MARKERS.search(spec.question)) or (
            bool(OPEN_ENDED_LEAD.match(spec.question.strip()))
            and bool(meaningful_focus)
            and not spec.group_dimension_is_explicit()
        )

        # A question that wants a number about unrecorded content needs a
        # per-trace judgment, even when it is phrased in content language:
        # clustering cannot answer "how many".
        wants_a_number = bool(COUNTING_MARKERS.search(normalized)) or bool(YES_NO_MARKERS.match(spec.question.strip()))
        if spec.group_dimension_is_explicit():
            # "…by outcome" names a recorded column, so the answer is a
            # breakdown of that column, never a new per-trace judgment.
            spec.path = "layer1"
            return
        if meaningful_focus and wants_a_number:
            spec.path = "layer3_aspect"
            spec.aspect_question = _aspect_question(spec.question)
            spec.aspect_type = "boolean"
            spec.group_by = None
            # A tool name in the question is a guess about *how* the content was
            # handled. The aspect reads the content itself, so that guess would
            # silently shrink the denominator.
            tool_filters = [item for item in spec.predicates if item.field == "tool"]
            if tool_filters:
                spec.predicates = [item for item in spec.predicates if item.field != "tool"]
                spec.rationale.append(
                    "Dropped the tool-name filter: the aspect judges every eligible conversation's content rather "
                    "than assuming the answer lives behind one tool."
                )
            return

        if content_question and (not spec.group_dimension_is_explicit() or meaningful_focus):
            spec.path = "layer2_cluster"
            spec.group_by = None
            # Groups are emergent, so the only meaningful measure is how many
            # conversations fell into each one.
            spec.metric = "traces"
            spec.aggregate = "count"
            return

        if meaningful_focus and not spec.predicates and not spec.group_by:
            spec.path = "layer2_cluster"
            return

        if meaningful_focus and len(meaningful_focus) >= 3 and spec.metric == "traces" and not spec.group_by:
            spec.path = "layer2_cluster"
            return

        spec.path = "layer1"

    def _explain(self, spec: QuerySpec, normalized: str) -> None:
        if spec.path == "layer1":
            spec.rationale.append(
                f"Every part of this question maps to recorded trace shape, so Raft will {spec.describe()}."
            )
            if spec.matched_phrases:
                spec.rationale.append("Matched: " + ", ".join(sorted(set(spec.matched_phrases))[:6]) + ".")
        elif spec.path == "layer2_cluster":
            spec.rationale.append(
                "This asks about what people said, which no precomputed column can hold. Raft will embed and "
                f"cluster {spec.filter_summary()} and name the groups from the words those conversations actually use."
            )
            if spec.focus_terms:
                spec.rationale.append(
                    "Clustering is focused on: " + ", ".join(spec.focus_terms[:8]) + "."
                )
        else:
            spec.rationale.append(
                "No saved field answers this, so Raft will define a reusable per-trace aspect and evaluate it "
                "once over every eligible conversation."
            )


_FILLER = frozenset(
    """
    my our the a an me you your their there here get getting got give given show tell find see
    know knows about into from with for and but that this these those what which who when where why
    how many much most least more less than then them they people users user customers customer
    conversation conversations trace traces chat chats agent agents llm bot assistant thing things
    stuff anything something lot lots really actually just only also very often usually normally
    please could would should can cannot does did doing done was were are is be been being have has
    had happen happens happened going go goes went make makes made take takes took come comes came
    look looks looked want wants wanted need needs needed use uses used run runs running
    number numbers amount amounts count counts total totals percentage percent proportion share
    shares average averages avg mean means typical median highest lowest biggest smallest
    ask asks asked asking mention mentions mentioned say says said saying talk talks talked
    tell tells told common typical usual usually often kind kinds sort sorts type types
    end ends ended start starts started
    """.split()
)


def _residual(normalized: str, spans: list[tuple[int, int]]) -> str:
    characters = list(normalized)
    for start, end in spans:
        for position in range(max(0, start), min(len(characters), end)):
            characters[position] = " "
    return " ".join(word for word in "".join(characters).split() if len(word) > 2 and word not in _FILLER)


def _aspect_question(question: str) -> str:
    text = question.strip().rstrip("?")
    lowered = text.casefold()
    for prefix, template in (
        ("how many people are getting ", "Did the assistant give the user {rest}?"),
        ("how many people got ", "Did the assistant give the user {rest}?"),
        ("how many users got ", "Did the assistant give the user {rest}?"),
        ("how many users received ", "Did the user receive {rest}?"),
        ("how many people received ", "Did the user receive {rest}?"),
        ("how many conversations ", "Did this conversation {rest}?"),
        ("how many traces ", "Did this conversation {rest}?"),
        ("how many times ", "Did this conversation {rest}?"),
        ("how many ", "Did this conversation involve {rest}?"),
        ("how often does ", "Did the assistant {rest}?"),
        ("how often do ", "Did this conversation {rest}?"),
        ("how often ", "Did this conversation {rest}?"),
        ("what share of ", "Did this conversation involve {rest}?"),
        ("what percentage of ", "Did this conversation involve {rest}?"),
        ("what proportion of ", "Did this conversation involve {rest}?"),
    ):
        if lowered.startswith(prefix):
            rest = text[len(prefix):].strip()
            rest = re.sub(r"^(?:my|our|the)\s+", "", rest, flags=re.IGNORECASE)
            rest = re.sub(r"^(?:agent|assistant|bot|model|llm|it)\s+", "", rest, flags=re.IGNORECASE)
            rest = re.sub(r"\bfrom my llm\b|\bfrom the (?:agent|assistant|bot)\b", "", rest, flags=re.IGNORECASE).strip()
            return template.format(rest=rest).replace("  ", " ")
    if YES_NO_MARKERS.match(text):
        return text if text.endswith("?") else f"{text}?"
    return f"Is the following true of this conversation: {text}?"


# ---------------------------------------------------------------------------
# Catalog: real values present in this dataset
# ---------------------------------------------------------------------------


class DatasetCatalog:
    """Concrete values the compiler can pin a filter to, read from the data."""

    def __init__(self, values: dict[str, list[str]]):
        self.values = values
        self._phrases: list[tuple[str, Predicate]] = []
        for app in values.get("app", []):
            for phrase in _name_phrases(app):
                self._phrases.append((phrase, _pred("app", "t.app = ?", (app,), f"the app is {app}", id=f"app:{app}")))
        for tool in values.get("tool", []):
            for phrase in _name_phrases(tool):
                self._phrases.append(
                    (phrase, _pred("tool", "EXISTS (SELECT 1 FROM trace_tools x WHERE x.trace_id = t.id AND x.tool = ?)", (tool,), f"the {tool} tool was called", id=f"tool:{tool}"))
                )
        for model in values.get("model", []):
            short = model.split("/")[-1]
            for phrase in {model.casefold(), short.casefold()}:
                self._phrases.append((phrase, _pred("model", "t.model = ?", (model,), f"the model is {short}", id=f"model:{model}")))
        for code in values.get("error_code", []):
            if code:
                self._phrases.append((code.casefold(), _pred("error_code", "t.error_code = ?", (code,), f"the error code is {code}", id=f"error:{code}")))
        # Longest phrases first so "storefront support" beats "support".
        self._phrases.sort(key=lambda item: len(item[0]), reverse=True)

    @classmethod
    def from_db(cls, db) -> "DatasetCatalog":
        def column(sql: str) -> list[str]:
            return [str(row["value"]) for row in db.fetch_all(sql) if row["value"]]

        return cls(
            {
                "app": column("SELECT DISTINCT app AS value FROM traces"),
                "tool": column("SELECT DISTINCT tool AS value FROM trace_tools"),
                "model": column("SELECT DISTINCT model AS value FROM traces"),
                "error_code": column("SELECT DISTINCT error_code AS value FROM traces WHERE error_code IS NOT NULL"),
            }
        )

    @property
    def phrases(self) -> list[tuple[str, Predicate]]:
        return self._phrases

    def match(self, normalized: str) -> list[tuple[str, Predicate, tuple[int, int]]]:
        found: list[tuple[str, Predicate, tuple[int, int]]] = []
        taken: list[tuple[int, int]] = []
        for phrase, predicate in self._phrases:
            if len(phrase) < 4:
                continue
            position = normalized.find(phrase)
            if position < 0:
                continue
            span = (position, position + len(phrase))
            if any(start < span[1] and span[0] < end for start, end in taken):
                continue
            taken.append(span)
            found.append((phrase, predicate, span))
        return found


def _name_phrases(name: str) -> set[str]:
    lowered = name.casefold()
    return {lowered, lowered.replace("-", " "), lowered.replace("_", " ")}


# ---------------------------------------------------------------------------
# Deterministic aggregation source
# ---------------------------------------------------------------------------


AGGREGATION_HEADER = '''"""Raft aggregation. Written by Raft, never by a model.

Input rows carry one entry per eligible trace: its group key and the metric
value read from the trace table. This file only counts, sums and ranks.
"""
import json
from collections import defaultdict

rows = json.loads(INPUT_JSON)
'''


def aggregation_code(spec: QuerySpec) -> str:
    metric = spec.metric_definition
    aggregate = spec.aggregate
    body = f'''
METRIC = {metric.key!r}
AGGREGATE = {aggregate!r}
UNIT = {metric.unit!r}

eligible = [row for row in rows if row.get("eligible", True)]
denominator = len(eligible)

totals = defaultdict(float)
counts = defaultdict(int)
for row in eligible:
    key = str(row["group"])
    counts[key] += 1
    value = row.get("value")
    if value is not None:
        totals[key] += float(value)

def summarise(key):
    if AGGREGATE == "count":
        return float(counts[key])
    if AGGREGATE == "avg":
        return totals[key] / counts[key] if counts[key] else 0.0
    return totals[key]

groups = [
    {{
        "key": key,
        "count": counts[key],
        "share": counts[key] / denominator if denominator else 0.0,
        "value": round(summarise(key), 6),
    }}
    for key in counts
]
groups.sort(key=lambda group: (-group["value"], -group["count"], group["key"]))

value_sum = round(sum(totals.values()), 6)
result = {{
    "denominator": denominator,
    "value_sum": value_sum,
    "value_mean": round(value_sum / denominator, 6) if denominator else 0.0,
    "metric": METRIC,
    "aggregate": AGGREGATE,
    "unit": UNIT,
    "groups": groups,
}}
print(json.dumps(result, sort_keys=True))
'''
    return AGGREGATION_HEADER + body


def format_metric(value: float, unit: str, reference: float | None = None) -> str:
    """`reference` is the largest value in the same answer, so one list of
    numbers uses one precision instead of mixing $0.01 with $0.0026."""
    if unit == "usd":
        scale = reference if reference is not None else value
        decimals = 2 if scale >= 1 else 3 if scale >= 0.1 else 4
        return f"${value:,.{decimals}f}"
    if unit == "ms":
        return f"{value / 1000:,.1f}s"
    if unit == "tokens":
        return f"{value:,.0f} tokens"
    if unit == "count":
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def spec_to_json(spec: QuerySpec) -> str:
    return json.dumps(
        {
            "question": spec.question,
            "path": spec.path,
            "metric": spec.metric,
            "aggregate": spec.aggregate,
            "group_by": spec.group_by,
            "filters": [item.label for item in spec.predicates],
            "focus": spec.focus,
            "aspect_question": spec.aspect_question,
        },
        indent=2,
        sort_keys=True,
    )


def filter_catalog(catalog: "DatasetCatalog") -> list[dict[str, str]]:
    """Every filter Raft can apply, as a menu a model can pick from by id.

    The planner never writes SQL. It chooses ids from this list, Raft validates
    them against its own registry, and the SQL is built here as always - so a
    model can interpret "what do customers complain about" without being able to
    reach the database.
    """
    menu = [{"id": predicate.id, "label": predicate.label} for _, predicate in LEXICON if predicate.id]
    for phrase, predicate in catalog.phrases:
        if predicate.id and not any(item["id"] == predicate.id for item in menu):
            menu.append({"id": predicate.id, "label": predicate.label})
    return menu


def predicate_by_id(catalog: "DatasetCatalog", identifier: str) -> Predicate | None:
    for _, predicate in LEXICON:
        if predicate.id == identifier:
            return predicate
    for _, predicate in catalog.phrases:
        if predicate.id == identifier:
            return predicate
    return None
