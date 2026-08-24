from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
  id TEXT PRIMARY KEY,
  app TEXT NOT NULL,
  started_at TEXT NOT NULL,
  duration_ms INTEGER,
  model TEXT NOT NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL,
  capture_completeness TEXT NOT NULL,
  cost_usd REAL NOT NULL,
  tokens_input INTEGER NOT NULL,
  tokens_output INTEGER NOT NULL,
  outcome TEXT NOT NULL,
  failure_mode TEXT NOT NULL,
  summary TEXT NOT NULL,
  user_request TEXT NOT NULL,
  what_happened TEXT NOT NULL,
  verbatim_quote TEXT NOT NULL,
  user_text TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL,
  intent_key TEXT NOT NULL DEFAULT '',
  intent_label TEXT NOT NULL DEFAULT '',
  turns INTEGER NOT NULL DEFAULT 0,
  tool_calls INTEGER NOT NULL DEFAULT 0,
  distinct_tools TEXT NOT NULL DEFAULT '[]',
  repeated_identical_calls INTEGER NOT NULL DEFAULT 0,
  rephrase_count INTEGER NOT NULL DEFAULT 0,
  user_gave_up INTEGER NOT NULL DEFAULT 0,
  intent_satisfied TEXT NOT NULL DEFAULT 'unclear',
  sentiment_end TEXT NOT NULL DEFAULT 'unclear',
  ended_by TEXT NOT NULL DEFAULT 'assistant',
  last_span_type TEXT NOT NULL DEFAULT 'assistant_message',
  error_code TEXT,
  redaction_status TEXT NOT NULL,
  guardrail_status TEXT NOT NULL,
  autopsy TEXT NOT NULL,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_traces_outcome ON traces(outcome);
CREATE INDEX IF NOT EXISTS idx_traces_failure ON traces(failure_mode);
CREATE INDEX IF NOT EXISTS idx_traces_app ON traces(app);
CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at);

CREATE TABLE IF NOT EXISTS trace_tools (
  trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
  tool TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (trace_id, tool)
);
CREATE INDEX IF NOT EXISTS idx_trace_tools_tool ON trace_tools(tool);

CREATE TABLE IF NOT EXISTS spans (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  type TEXT NOT NULL,
  name TEXT,
  duration_ms INTEGER,
  tokens_in INTEGER NOT NULL,
  tokens_out INTEGER NOT NULL,
  cost_usd REAL NOT NULL,
  status TEXT NOT NULL,
  content_redacted TEXT NOT NULL,
  error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, idx);

CREATE TABLE IF NOT EXISTS trace_label_provenance (
  trace_id TEXT PRIMARY KEY REFERENCES traces(id) ON DELETE CASCADE,
  request_id TEXT,
  model_id TEXT NOT NULL,
  provider TEXT,
  prompt_version TEXT NOT NULL,
  schema_valid INTEGER NOT NULL,
  quote_valid INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_provenance (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_runs (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  request_id TEXT,
  interpretation TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_members (
  cluster_run_id TEXT NOT NULL REFERENCES cluster_runs(id) ON DELETE CASCADE,
  trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
  cluster_key TEXT NOT NULL,
  cluster_label TEXT NOT NULL,
  PRIMARY KEY(cluster_run_id, trace_id)
);

CREATE TABLE IF NOT EXISTS aspects (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  type TEXT NOT NULL,
  labels_json TEXT NOT NULL DEFAULT '[]',
  created_from TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aspect_values (
  trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
  aspect_id TEXT NOT NULL REFERENCES aspects(id) ON DELETE CASCADE,
  value_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  status TEXT NOT NULL,
  cost_usd REAL NOT NULL DEFAULT 0,
  request_id TEXT,
  score REAL,
  evidence_quote TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (trace_id, aspect_id)
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  path TEXT NOT NULL,
  status TEXT NOT NULL,
  aspect_id TEXT,
  estimate_usd REAL NOT NULL DEFAULT 0,
  estimate_seconds_min INTEGER NOT NULL DEFAULT 0,
  estimate_seconds_max INTEGER NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  answer_id TEXT,
  error TEXT,
  planner_note TEXT NOT NULL DEFAULT '',
  spec_json TEXT NOT NULL DEFAULT '{}',
  parent_run_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answers (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  question TEXT NOT NULL,
  path TEXT NOT NULL,
  denominator INTEGER NOT NULL,
  excluded_count INTEGER NOT NULL,
  groups_json TEXT NOT NULL,
  interpretation TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  work_json TEXT NOT NULL,
  aspect_id TEXT,
  spec_json TEXT NOT NULL DEFAULT '{}',
  headline TEXT NOT NULL DEFAULT '',
  metric TEXT NOT NULL DEFAULT 'traces',
  unit TEXT NOT NULL DEFAULT 'count',
  follow_ups_json TEXT NOT NULL DEFAULT '[]',
  standouts_json TEXT NOT NULL DEFAULT '[]',
  is_overview INTEGER NOT NULL DEFAULT 0,
  suggestions_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL,
  requested_model TEXT NOT NULL,
  final_model TEXT,
  provider TEXT,
  request_id TEXT,
  latency_ms INTEGER,
  cost_usd REAL,
  status TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_evidence (
  feature TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  request_id TEXT,
  notes TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._add_missing_columns(connection)
            # This index targets a column introduced after the first demo
            # database was created. Create it only after the in-place column
            # migration has run, otherwise an old volume cannot start.
            connection.execute("CREATE INDEX IF NOT EXISTS idx_traces_satisfied ON traces(intent_satisfied)")
            for feature in ("Guardrails", "Routing", "MCP Servers", "Budgets", "Code Execution", "Web Search Enablement"):
                connection.execute(
                    "INSERT OR IGNORE INTO feature_evidence(feature,status,notes,updated_at) VALUES(?,?,?,?)",
                    (feature, "pending_live_evidence", "Implementation present; no live request ID recorded.", utc_now()),
                )

    @staticmethod
    def _add_missing_columns(connection: sqlite3.Connection) -> None:
        """Bring an older database file up to the current schema in place."""
        wanted: dict[str, dict[str, str]] = {
            "runs": {
                "planner_note": "TEXT NOT NULL DEFAULT ''",
                "spec_json": "TEXT NOT NULL DEFAULT '{}'",
                "parent_run_id": "TEXT",
            },
            "answers": {
                "spec_json": "TEXT NOT NULL DEFAULT '{}'",
                "headline": "TEXT NOT NULL DEFAULT ''",
                "metric": "TEXT NOT NULL DEFAULT 'traces'",
                "unit": "TEXT NOT NULL DEFAULT 'count'",
                "follow_ups_json": "TEXT NOT NULL DEFAULT '[]'",
                "standouts_json": "TEXT NOT NULL DEFAULT '[]'",
                "is_overview": "INTEGER NOT NULL DEFAULT 0",
                "suggestions_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "aspects": {
                "labels_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "aspect_values": {
                "score": "REAL",
                "evidence_quote": "TEXT NOT NULL DEFAULT ''",
            },
            "traces": {
                "user_text": "TEXT NOT NULL DEFAULT ''",
                "intent_key": "TEXT NOT NULL DEFAULT ''",
                "intent_label": "TEXT NOT NULL DEFAULT ''",
                "turns": "INTEGER NOT NULL DEFAULT 0",
                "tool_calls": "INTEGER NOT NULL DEFAULT 0",
                "distinct_tools": "TEXT NOT NULL DEFAULT '[]'",
                "repeated_identical_calls": "INTEGER NOT NULL DEFAULT 0",
                "rephrase_count": "INTEGER NOT NULL DEFAULT 0",
                "user_gave_up": "INTEGER NOT NULL DEFAULT 0",
                "intent_satisfied": "TEXT NOT NULL DEFAULT 'unclear'",
                "sentiment_end": "TEXT NOT NULL DEFAULT 'unclear'",
                "ended_by": "TEXT NOT NULL DEFAULT 'assistant'",
                "last_span_type": "TEXT NOT NULL DEFAULT 'assistant_message'",
                "error_code": "TEXT",
            },
        }
        for table, columns in wanted.items():
            present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            if not present:
                continue
            for name, definition in columns.items():
                if name not in present:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as connection:
            connection.execute(query, params)

    def execute_many(self, query: str, values: list[tuple[Any, ...]]) -> None:
        with self.connect() as connection:
            connection.executemany(query, values)

    def trace_count(self) -> int:
        row = self.fetch_one("SELECT COUNT(*) AS count FROM traces")
        return int(row["count"]) if row else 0

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM runs WHERE id=?", (run_id,))

    def update_run(self, run_id: str, **values: Any) -> None:
        values["updated_at"] = utc_now()
        fields = ", ".join(f"{key}=?" for key in values)
        self.execute(
            f"UPDATE runs SET {fields} WHERE id=?",
            tuple(values.values()) + (run_id,),
        )

    def get_answer_for_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM answers WHERE run_id=?", (run_id,))
        if not row:
            return None
        row["groups"] = json.loads(row.pop("groups_json"))
        row["evidence"] = json.loads(row.pop("evidence_json"))
        row["work"] = json.loads(row.pop("work_json"))
        row["spec"] = json.loads(row.pop("spec_json", "{}") or "{}")
        row["follow_ups"] = json.loads(row.pop("follow_ups_json", "[]") or "[]")
        row["standouts"] = json.loads(row.pop("standouts_json", "[]") or "[]")
        row["suggestions"] = json.loads(row.pop("suggestions_json", "[]") or "[]")
        row["is_overview"] = bool(row.get("is_overview"))
        return row

    def record_feature(self, feature: str, status: str, request_id: str | None, notes: str) -> None:
        current = self.fetch_one("SELECT notes FROM feature_evidence WHERE feature=?", (feature,))
        prior = current["notes"] if current else ""
        combined = notes if not prior or notes in prior else f"{prior} | {notes}"
        self.execute(
            """
            INSERT INTO feature_evidence(feature,status,request_id,notes,updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(feature) DO UPDATE SET
              status=excluded.status,request_id=excluded.request_id,notes=excluded.notes,updated_at=excluded.updated_at
            """,
            (feature, status, request_id, combined, utc_now()),
        )
