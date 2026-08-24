from __future__ import annotations

import sqlite3

from raft.db import Database


def test_initialize_migrates_legacy_traces_before_new_index(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE traces (
              id TEXT PRIMARY KEY,
              app TEXT NOT NULL,
              started_at TEXT NOT NULL,
              outcome TEXT NOT NULL,
              failure_mode TEXT NOT NULL
            );
            """
        )

    Database(path).initialize()

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(traces)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(traces)")}

    assert "intent_satisfied" in columns
    assert "idx_traces_satisfied" in indexes
