"""The dataset has to be varied enough that an unanticipated question lands.

It also has to be internally honest: every shape column must be true of the
spans stored next to it, because the whole product rests on that.
"""

from __future__ import annotations

import json
from pathlib import Path

from raft.db import Database
from raft.demo import build_dataset


def build(tmp_path: Path, count: int = 400) -> Database:
    db = Database(tmp_path / "corpus.db")
    db.initialize()
    build_dataset(db, count=count, seed=7)
    return db


def test_variety_is_high_enough_to_cluster(tmp_path: Path) -> None:
    db = build(tmp_path)
    distinct = db.fetch_one("SELECT COUNT(DISTINCT user_request) AS count FROM traces")
    # A scenario list with a handful of sentences would fail this outright.
    assert int(distinct["count"]) > 150
    assert int(db.fetch_one("SELECT COUNT(DISTINCT app) AS c FROM traces")["c"]) >= 6
    assert int(db.fetch_one("SELECT COUNT(DISTINCT intent_key) AS c FROM traces")["c"]) >= 25
    assert int(db.fetch_one("SELECT COUNT(DISTINCT failure_mode) AS c FROM traces")["c"]) >= 7


def test_shape_columns_match_the_stored_spans(tmp_path: Path) -> None:
    db = build(tmp_path, count=200)
    for trace in db.fetch_all("SELECT * FROM traces"):
        spans = db.fetch_all("SELECT * FROM spans WHERE trace_id=? ORDER BY idx", (trace["id"],))
        assert spans, trace["id"]
        assert trace["turns"] == sum(1 for span in spans if span["type"] == "user_message")
        assert trace["tool_calls"] == sum(1 for span in spans if span["type"] == "tool_call")
        assert trace["last_span_type"] == spans[-1]["type"]
        assert json.loads(trace["distinct_tools"]) == sorted(
            {span["name"] for span in spans if span["type"] == "tool_call" and span["name"]}
        )
        assert trace["cost_usd"] == round(sum(span["cost_usd"] for span in spans), 6)
        assert trace["tokens_input"] == sum(span["tokens_in"] for span in spans)


def test_quote_is_a_literal_substring_of_a_span(tmp_path: Path) -> None:
    db = build(tmp_path, count=200)
    for trace in db.fetch_all("SELECT id, verbatim_quote, user_request FROM traces"):
        spans = db.fetch_all("SELECT content_redacted FROM spans WHERE trace_id=?", (trace["id"],))
        haystack = "\n".join(span["content_redacted"] for span in spans)
        assert trace["verbatim_quote"] in haystack
        assert trace["user_request"] in haystack


def test_pii_is_redacted_before_it_is_ever_stored(tmp_path: Path) -> None:
    db = build(tmp_path, count=400)
    for table, column in (("traces", "user_request"), ("spans", "content_redacted")):
        rows = db.fetch_all(f"SELECT {column} AS text FROM {table}")
        joined = " ".join(row["text"] for row in rows)
        assert "@example.com" not in joined
        assert "sk-live" not in joined
        assert "+44 7700" not in joined
    redacted = db.fetch_one("SELECT COUNT(*) AS c FROM traces WHERE redaction_status='redacted'")
    assert int(redacted["c"]) > 0  # the placeholders got there by redaction, not omission


def test_tool_timing_is_never_invented(tmp_path: Path) -> None:
    db = build(tmp_path, count=200)
    # A base-URL proxy sees LLM latency, never the application's own tool time.
    rows = db.fetch_all("SELECT duration_ms FROM spans WHERE type IN ('tool_call','tool_result')")
    assert rows and all(row["duration_ms"] is None for row in rows)


def test_regeneration_with_the_same_seed_is_identical(tmp_path: Path) -> None:
    first = build(tmp_path / "a", count=120)
    second = build(tmp_path / "b", count=120)
    (tmp_path / "a").mkdir(exist_ok=True)
    assert [row["user_request"] for row in first.fetch_all("SELECT user_request FROM traces ORDER BY id")] == [
        row["user_request"] for row in second.fetch_all("SELECT user_request FROM traces ORDER BY id")
    ]


def test_a_database_from_an_older_build_is_rebuilt(tmp_path: Path) -> None:
    """Docker keeps its volume across `up --build`.

    Without a version check the app then answers every question from a corpus
    whose shape columns the current code cannot read - which showed up as one
    identical answer for every question typed.
    """
    from raft.demo import CORPUS_VERSION, dataset_is_current, seed_demo

    db = build(tmp_path, count=150)
    assert dataset_is_current(db)[0]

    # Age it exactly the way an upgrade does: columns added with defaults, no
    # version marker.
    db.execute("UPDATE traces SET intent_key='', user_text='', intent_satisfied='unclear', user_gave_up=0")
    db.execute("DELETE FROM dataset_provenance WHERE key='corpus_version'")
    current, reason = dataset_is_current(db)
    assert not current and "corpus version" in reason

    assert seed_demo(db, 150, 7).startswith("rebuilt")
    assert dataset_is_current(db)[0]
    assert int(db.fetch_one("SELECT COUNT(*) AS c FROM traces WHERE intent_key = ''")["c"]) == 0
    assert int(db.fetch_one("SELECT COUNT(*) AS c FROM traces WHERE intent_satisfied = 'no'")["c"]) > 0
    assert seed_demo(db, 150, 7) == "kept"


def test_rebuilding_discards_answers_that_described_the_old_traces(tmp_path: Path) -> None:
    from raft.db import utc_now
    from raft.demo import reset_dataset

    db = build(tmp_path, count=120)
    db.execute(
        "INSERT INTO runs(id,question,path,status,estimate_usd,estimate_seconds_min,estimate_seconds_max,"
        "completed,total,message,created_at,updated_at) VALUES('r1','q','layer1','complete',0,0,0,1,1,'',?,?)",
        (utc_now(), utc_now()),
    )
    reset_dataset(db)
    assert db.trace_count() == 0
    assert db.fetch_all("SELECT id FROM runs") == []
