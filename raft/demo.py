"""Demo dataset seeding.

The traces are generated from `raft.corpus`, which builds a conversation first
and derives every label from it. That matters for the product claim: shape
fields, quotes and clusters all describe the same stored spans, so a question
nobody anticipated still lands on real content instead of a scenario label.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import numpy as np

from raft.corpus import APPS, SpanDraft, build_episode
from raft.db import Database, utc_now
from raft.embedding import SemanticIndex
from raft.redaction import redact_text


# Catalog dollars per million input / output tokens, from the PRD snapshot.
MODEL_RATES = {
    "mzai:nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "mzai:Qwen/Qwen3-30B-A3B-Instruct-2507": (0.10, 0.30),
    "mzai:Qwen/Qwen3-32B": (0.10, 0.30),
    "mzai:Qwen/Qwen3-235B-A22B-Instruct-2507": (0.20, 0.60),
    "mzai:google/gemma-3-27b-it": (0.10, 0.30),
    "mzai:meta-llama/Llama-3.3-70B-Instruct": (0.13, 0.40),
    "mzai:openai/gpt-oss-120b": (0.15, 0.60),
}

PII_SUFFIXES = (
    " You can reach me at maya@example.com or +31 6 1234 5678.",
    " My email is j.okonkwo@example.org if that helps.",
    " Call me on +44 7700 900123 any time before six.",
    " The API key I used was sk-live-9f2b71c4ad55e0.",
)

# Output tokens per span type. Input is dominated by the system prompt plus the
# conversation replayed on every turn, which is where real spend actually goes.
TOKEN_PROFILE = {
    "user_message": (0, 0),
    "assistant_message": (60, 260),
    "thinking": (40, 220),
    "tool_call": (20, 90),
    "tool_result": (0, 0),
    "error": (0, 0),
}
SYSTEM_PROMPT_TOKENS = 900


def _document(user_request: str, spans: list[SpanDraft]) -> str:
    """Text used for embedding: what the user asked plus what the agent did."""
    parts = [user_request]
    for span in spans:
        if span.type in ("user_message", "assistant_message"):
            parts.append(span.content)
        elif span.type == "tool_call" and span.name:
            parts.append(f"tool {span.name}")
        elif span.type == "error":
            parts.append(span.content)
    return " ".join(parts)


# Bump whenever the generator changes in a way that makes older rows wrong:
# new columns, new intents, different labelling. A database built by an earlier
# version is not "existing data to preserve", it is data whose shape columns are
# now schema defaults, and every content filter silently matches nothing.
CORPUS_VERSION = "2"


def dataset_is_current(db: Database) -> tuple[bool, str]:
    """Whether the stored demo data was built by this version of the generator."""
    if not db.trace_count():
        return False, "empty"
    row = db.fetch_one("SELECT value FROM dataset_provenance WHERE key='corpus_version'")
    stored = row["value"] if row else None
    if stored != CORPUS_VERSION:
        return False, f"built by corpus version {stored or 'unknown'}, current is {CORPUS_VERSION}"
    unlabelled = db.fetch_one("SELECT COUNT(*) AS count FROM traces WHERE intent_key = ''")
    if unlabelled and int(unlabelled["count"]):
        return False, f"{int(unlabelled['count'])} rows are missing generated labels"
    return True, "current"


def seed_demo(db: Database, count: int = 847, seed: int = 20260821) -> str:
    """Build the demo dataset, replacing anything an older version left behind.

    Docker keeps `raft-data` across `up --build`, so without this check a
    long-running install answers every question from a corpus that no longer
    matches the code reading it.
    """
    current, reason = dataset_is_current(db)
    if current:
        return "kept"
    if db.trace_count():
        reset_dataset(db)
    build_dataset(db, count=count, seed=seed)
    return f"rebuilt ({reason})"


def reset_dataset(db: Database) -> None:
    """Drop every derived artefact along with the traces they describe."""
    for table in (
        "answers", "runs", "aspect_values", "aspects", "cluster_members",
        "cluster_runs", "trace_tools", "spans", "traces",
    ):
        db.execute(f"DELETE FROM {table}")


def build_dataset(db: Database, *, count: int = 847, seed: int = 20260821, use_bge: bool = False) -> dict:
    rng = random.Random(seed)
    base_time = datetime(2026, 8, 21, 18, 0, tzinfo=UTC)
    app_weights = [app.weight for app in APPS]

    traces: list[dict] = []
    spans_by_trace: list[list[SpanDraft]] = []

    for index in range(count):
        app = rng.choices(APPS, weights=app_weights, k=1)[0]
        intent = rng.choice(app.intents)
        episode = build_episode(rng, app, intent)

        spans = list(episode.spans)
        if index % 61 == 0:
            suffix = PII_SUFFIXES[(index // 61) % len(PII_SUFFIXES)]
            spans[0] = SpanDraft(spans[0].type, spans[0].name, spans[0].content + suffix)

        original_first = spans[0].content
        redacted_spans = [
            SpanDraft(span.type, span.name, redact_text(span.content).text, span.status, span.error_code)
            for span in spans
        ]
        user_request = redacted_spans[0].content

        # Timing and cost follow the shape of the conversation.
        tokens_input = 0
        tokens_output = 0
        span_rows: list[dict] = []
        elapsed = 0
        transcript_tokens = SYSTEM_PROMPT_TOKENS + rng.randint(0, 600)
        input_rate, output_rate = MODEL_RATES.get(episode.model, (0.15, 0.60))
        for position, span in enumerate(redacted_spans):
            low, high = TOKEN_PROFILE.get(span.type, (0, 0))
            words = max(1, len(span.content.split()))
            span_tokens = int(words * 1.35) + 4
            tokens_out = int(rng.uniform(low, high) * 0.01 * span_tokens + span_tokens) if high else 0
            # Every model call re-reads the whole transcript so far.
            tokens_in = transcript_tokens if span.type in ("assistant_message", "thinking", "tool_call") else 0
            transcript_tokens += span_tokens
            tokens_input += tokens_in
            tokens_output += tokens_out
            # A base-URL proxy sees LLM latency; it never sees application tool time.
            duration = None if span.type in ("tool_call", "tool_result") else rng.randint(240, 2600)
            if duration:
                elapsed += duration
            cost = round((tokens_in * input_rate + tokens_out * output_rate) / 1_000_000, 8)
            span_rows.append(
                {
                    "type": span.type,
                    "name": span.name,
                    "duration_ms": duration,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_usd": cost,
                    "status": span.status,
                    "content": span.content,
                    "error_code": span.error_code,
                    "index": position,
                }
            )
        cost_usd = round(sum(row["cost_usd"] for row in span_rows), 6)
        duration_ms = max(elapsed, 400) + rng.randint(0, 900)

        started_at = (base_time - timedelta(minutes=rng.randint(0, 60 * 24 * 21), seconds=rng.randint(0, 59))).isoformat()
        tools = [row["name"] for row in span_rows if row["type"] == "tool_call" and row["name"]]
        tool_counts: dict[str, int] = {}
        for tool in tools:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
        turns = sum(1 for row in span_rows if row["type"] == "user_message")

        quote = _select_quote(redacted_spans)
        traces.append(
            {
                "id": f"tr_{index + 1:04d}",
                "app": episode.app,
                "started_at": started_at,
                "duration_ms": duration_ms,
                "model": episode.model,
                "provider": episode.provider,
                "status": "error" if episode.error_code else "ok",
                "capture_completeness": "incomplete" if (index % 113 == 0) else "complete",
                "cost_usd": cost_usd,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "outcome": episode.outcome,
                "failure_mode": episode.failure_mode,
                "summary": episode.summary,
                "user_request": user_request,
                "what_happened": episode.what_happened,
                "verbatim_quote": quote,
                "user_text": " ".join(
                    span.content for span in redacted_spans if span.type == "user_message"
                ),
                "topic": episode.intent_key,
                "intent_key": episode.intent_key,
                "intent_label": episode.intent_label,
                "turns": turns,
                "tool_calls": len(tools),
                "distinct_tools": json.dumps(sorted(tool_counts)),
                "repeated_identical_calls": episode.repeated_identical_calls,
                "rephrase_count": episode.rephrase_count,
                "user_gave_up": int(episode.user_gave_up),
                "intent_satisfied": episode.intent_satisfied,
                "sentiment_end": episode.sentiment_end,
                "ended_by": episode.ended_by,
                "last_span_type": span_rows[-1]["type"],
                "error_code": episode.error_code,
                "redaction_status": "redacted" if user_request != original_first else "clean",
                "guardrail_status": "not_live_checked",
                "autopsy": _autopsy(episode, tokens_input + tokens_output, cost_usd, duration_ms, tool_counts),
                "_spans": span_rows,
                "_tools": tool_counts,
                "_document": _document(user_request, redacted_spans),
            }
        )
        spans_by_trace.append(redacted_spans)

    index_model = SemanticIndex.fit([trace["_document"] for trace in traces], use_bge=use_bge)
    vectors = index_model.encode([trace["_document"] for trace in traces])

    trace_rows = []
    span_rows_flat = []
    tool_rows = []
    for trace, vector in zip(traces, vectors, strict=True):
        trace_rows.append(
            (
                trace["id"], trace["app"], trace["started_at"], trace["duration_ms"], trace["model"],
                trace["provider"], trace["status"], trace["capture_completeness"], trace["cost_usd"],
                trace["tokens_input"], trace["tokens_output"], trace["outcome"], trace["failure_mode"],
                trace["summary"], trace["user_request"], trace["what_happened"], trace["verbatim_quote"], trace["user_text"],
                trace["topic"], trace["intent_key"], trace["intent_label"], trace["turns"], trace["tool_calls"],
                trace["distinct_tools"], trace["repeated_identical_calls"], trace["rephrase_count"],
                trace["user_gave_up"], trace["intent_satisfied"], trace["sentiment_end"], trace["ended_by"],
                trace["last_span_type"], trace["error_code"], trace["redaction_status"], trace["guardrail_status"],
                trace["autopsy"], np.asarray(vector, dtype=np.float32).tobytes(),
            )
        )
        for row in trace["_spans"]:
            span_rows_flat.append(
                (
                    f"{trace['id']}_sp_{row['index']}", trace["id"], row["index"], row["type"], row["name"],
                    row["duration_ms"], row["tokens_in"], row["tokens_out"], row["cost_usd"], row["status"],
                    row["content"], row["error_code"],
                )
            )
        for tool, calls in trace["_tools"].items():
            tool_rows.append((trace["id"], tool, calls))

    db.execute_many(
        """
        INSERT INTO traces(
          id,app,started_at,duration_ms,model,provider,status,capture_completeness,cost_usd,
          tokens_input,tokens_output,outcome,failure_mode,summary,user_request,what_happened,
          verbatim_quote,user_text,topic,intent_key,intent_label,turns,tool_calls,distinct_tools,
          repeated_identical_calls,rephrase_count,user_gave_up,intent_satisfied,sentiment_end,
          ended_by,last_span_type,error_code,redaction_status,guardrail_status,autopsy,embedding
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        trace_rows,
    )
    db.execute_many(
        """
        INSERT INTO spans(
          id,trace_id,idx,type,name,duration_ms,tokens_in,tokens_out,cost_usd,status,
          content_redacted,error_code
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        span_rows_flat,
    )
    db.execute_many("INSERT INTO trace_tools(trace_id,tool,calls) VALUES(?,?,?)", tool_rows)
    for key, value in (
        ("corpus_version", CORPUS_VERSION),
        ("generator", "raft.corpus compositional episodes"),
        ("embedding_model", index_model.backend),
        ("embedding_dims", str(index_model.dims)),
        ("vocabulary_terms", str(len(index_model.vocabulary))),
        ("seed", str(seed)),
        ("distinct_user_requests", str(len({trace["user_request"] for trace in traces}))),
    ):
        db.execute(
            "INSERT INTO dataset_provenance(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, value, utc_now()),
        )
    return {
        "traces": len(trace_rows),
        "spans": len(span_rows_flat),
        "distinct_user_requests": len({trace["user_request"] for trace in traces}),
        "embedding_backend": index_model.backend,
    }


def _select_quote(spans: list[SpanDraft]) -> str:
    """The most load-bearing user sentence: the last thing they said, or the ask."""
    user_spans = [span for span in spans if span.type == "user_message"]
    chosen = user_spans[-1] if len(user_spans) > 1 else user_spans[0]
    sentences = [part.strip() for part in _split_sentences(chosen.content) if len(part.strip()) > 12]
    return sentences[0] if sentences else chosen.content


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for character in text:
        current.append(character)
        if character in ".?!":
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def _autopsy(episode, tokens: int, cost: float, duration_ms: int, tool_counts: dict[str, int]) -> str:
    tool_note = (
        "No tool was called. "
        if not tool_counts
        else "Tools called: " + ", ".join(f"{name} x{count}" for name, count in sorted(tool_counts.items())) + ". "
    )
    return (
        f"The user opened with: “{episode.user_request}” {episode.what_happened} {tool_note}"
        f"The conversation ran {tokens:,} tokens for ${cost:.4f} over {duration_ms / 1000:.1f} seconds of observed "
        f"model latency and ended {'because the user stopped replying' if episode.user_gave_up else f'with the {episode.ended_by}'}."
    )
